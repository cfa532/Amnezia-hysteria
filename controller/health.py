#!/usr/bin/env python3
"""
Regional VPN health controller.

Every CHECK_INTERVAL seconds:
  - SSH into each server, check awg-quick@awg0 is active
  - Count active peers (handshake within 180s) via awg show awg0 dump
  - Drive DNS state machine (fail/recover thresholds)
  - Write /var/run/vpn-health.json for provision.py to read
"""

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/var/log/vpn-controller.log"),
    ],
)
log = logging.getLogger(__name__)

CONFIG_PATH  = Path("/etc/vpn-controller/controller.yaml")
HEALTH_STATE = Path("/var/run/vpn-health.json")

PEER_SESSION_WINDOW = 180   # seconds — WireGuard session liveness window


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    return {
        "dns": {
            "cf_api_token": os.environ["CF_API_TOKEN"],
            "cf_zone_id":   os.environ["CF_ZONE_ID"],
            "record_name":  os.environ["DNS_RECORD_NAME"],
            "ttl":          int(os.environ.get("DNS_TTL", 60)),
        },
        "check_interval":    int(os.environ.get("CHECK_INTERVAL", 30)),
        "fail_threshold":    int(os.environ.get("FAIL_THRESHOLD", 3)),
        "recover_threshold": int(os.environ.get("RECOVER_THRESHOLD", 2)),
        "servers":           json.loads(os.environ["SERVERS"]),
    }


@dataclass
class ServerState:
    name: str
    ip: str
    region: str
    max_peers: int
    ssh_key: str = ""
    ssh_pass: str = ""
    healthy: bool = True
    available: bool = True      # healthy AND active_peers < max_peers
    active_peers: int = 0
    dns_record_id: str = ""
    consecutive_failures: int = 0
    consecutive_successes: int = 0


# ── SSH helpers ────────────────────────────────────────────────────────────────

def _ssh_args(server: ServerState) -> list[str]:
    base = ["-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5"]
    if server.ssh_key:
        return ["ssh", "-i", server.ssh_key] + base + ["-o", "BatchMode=yes",
                f"root@{server.ip}"]
    elif server.ssh_pass:
        return ["sshpass", "-p", server.ssh_pass, "ssh"] + base + [f"root@{server.ip}"]
    raise RuntimeError(f"No SSH credentials for {server.name}")


def _ssh_check(server: ServerState) -> bool:
    try:
        args = _ssh_args(server) + ["systemctl is-active awg-quick@awg0"]
        r = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception as e:
        log.debug(f"Health check error for {server.name}: {e}")
        return False


def _count_active_peers(server: ServerState) -> int:
    """Count peers with a handshake within PEER_SESSION_WINDOW seconds."""
    try:
        args = _ssh_args(server) + ["awg show awg0 dump"]
        r = subprocess.run(args, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return 0
        now = time.time()
        count = 0
        lines = r.stdout.strip().splitlines()
        for line in lines[1:]:   # first line is the interface, skip it
            parts = line.split()
            # peer dump: pubkey preshared endpoint allowed-ips latest-handshake rx tx keepalive
            if len(parts) >= 5:
                try:
                    last_hs = int(parts[4])
                    if last_hs > 0 and (now - last_hs) < PEER_SESSION_WINDOW:
                        count += 1
                except ValueError:
                    pass
        return count
    except Exception as e:
        log.debug(f"Peer count error for {server.name}: {e}")
        return 0


# ── DNS ────────────────────────────────────────────────────────────────────────

class CloudflareDNS:
    def __init__(self, token: str, zone_id: str):
        self.zone_id = zone_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self.base = (f"https://api.cloudflare.com/client/v4/zones"
                     f"/{zone_id}/dns_records")

    def add(self, name: str, ip: str, ttl: int) -> str:
        r = requests.post(self.base, headers=self.headers,
                          json={"type": "A", "name": name,
                                "content": ip, "ttl": ttl})
        data = r.json()
        if data.get("success"):
            rid = data["result"]["id"]
            log.info(f"DNS  ADD  {name} → {ip} (id={rid})")
            return rid
        log.error(f"DNS add failed: {data.get('errors')}")
        return ""

    def remove(self, record_id: str, ip: str):
        r = requests.delete(f"{self.base}/{record_id}", headers=self.headers)
        if r.json().get("success"):
            log.info(f"DNS  DEL  record {record_id} ({ip})")
        else:
            log.error(f"DNS del failed: {r.json().get('errors')}")

    def list_records(self, name: str) -> list[dict]:
        r = requests.get(self.base, headers=self.headers,
                         params={"name": name, "type": "A"})
        return r.json().get("result", [])


def sync_initial_dns(servers: list[ServerState], dns: CloudflareDNS,
                     record_name: str, ttl: int):
    existing = {r["content"]: r["id"] for r in dns.list_records(record_name)}
    for server in servers:
        if server.ip in existing:
            server.dns_record_id = existing[server.ip]
            log.info(f"DNS existing: {server.name} ({server.ip})"
                     f" id={server.dns_record_id}")
        else:
            server.dns_record_id = dns.add(record_name, server.ip, ttl)


# ── Health state file (read by provision.py) ───────────────────────────────────

def write_health_state(servers: list[ServerState]):
    state = {
        "servers": {
            s.name: {
                "ip":           s.ip,
                "region":       s.region,
                "healthy":      s.healthy,
                "available":    s.available,
                "active_peers": s.active_peers,
                "max_peers":    s.max_peers,
            }
            for s in servers
        },
        "updated_at": time.time(),
    }
    HEALTH_STATE.write_text(json.dumps(state, indent=2))


# ── Main loop ──────────────────────────────────────────────────────────────────

def run():
    cfg = load_config()
    dns_cfg          = cfg["dns"]
    check_interval   = cfg["check_interval"]
    fail_threshold   = cfg["fail_threshold"]
    recover_threshold = cfg["recover_threshold"]
    record_name      = dns_cfg["record_name"]
    ttl              = dns_cfg["ttl"]

    servers = [
        ServerState(
            name=s["name"],
            ip=s["ip"],
            region=s.get("region", "default"),
            max_peers=s.get("max_peers", 50),
            ssh_key=s.get("ssh_key", ""),
            ssh_pass=s.get("ssh_pass", ""),
        )
        for s in cfg["servers"]
    ]

    dns = CloudflareDNS(dns_cfg["cf_api_token"], dns_cfg["cf_zone_id"])

    log.info(f"Controller starting — {len(servers)} servers, "
             f"interval={check_interval}s "
             f"fail={fail_threshold} recover={recover_threshold}")

    sync_initial_dns(servers, dns, record_name, ttl)

    while True:
        for server in servers:
            up = _ssh_check(server)

            if up:
                server.consecutive_failures = 0
                server.consecutive_successes = min(
                    server.consecutive_successes + 1, recover_threshold)
                server.active_peers = _count_active_peers(server)
                server.available = server.active_peers < server.max_peers

                if not server.healthy and \
                        server.consecutive_successes >= recover_threshold:
                    server.healthy = True
                    server.dns_record_id = dns.add(record_name, server.ip, ttl)
                    log.warning(f"RECOVER {server.name} ({server.ip})"
                                f" peers={server.active_peers}/{server.max_peers}"
                                f" — added to DNS")
            else:
                server.consecutive_successes = 0
                server.consecutive_failures = min(
                    server.consecutive_failures + 1, fail_threshold)
                server.active_peers = 0
                server.available = False

                if server.healthy and \
                        server.consecutive_failures >= fail_threshold:
                    server.healthy = False
                    if server.dns_record_id:
                        dns.remove(server.dns_record_id, server.ip)
                        server.dns_record_id = ""
                    log.warning(f"FAIL    {server.name} ({server.ip})"
                                f" — removed from DNS")

        summary = [
            f"{s.name}({'up' if s.healthy else 'down'}"
            f",{'avail' if s.available else 'full'}"
            f",peers={s.active_peers})"
            for s in servers
        ]
        log.info(f"Status: {' '.join(summary)}")
        write_health_state(servers)
        time.sleep(check_interval)


if __name__ == "__main__":
    run()
