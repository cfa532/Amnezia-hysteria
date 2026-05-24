#!/usr/bin/env python3
"""
Regional VPN health controller.

Polls each server's health sidecar every CHECK_INTERVAL seconds.
After FAIL_THRESHOLD consecutive failures, removes server from DNS.
After RECOVER_THRESHOLD consecutive successes, adds server back to DNS.

Config via environment variables or controller.yaml in the same directory.
"""

import os
import sys
import time
import logging
import json
import requests
import yaml
from dataclasses import dataclass, field
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/var/log/vpn-controller.log"),
    ],
)
log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "controller.yaml"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    # Fall back to environment variables
    return {
        "dns": {
            "cf_api_token": os.environ["CF_API_TOKEN"],
            "cf_zone_id": os.environ["CF_ZONE_ID"],
            "record_name": os.environ["DNS_RECORD_NAME"],
            "ttl": int(os.environ.get("DNS_TTL", 60)),
        },
        "check_interval": int(os.environ.get("CHECK_INTERVAL", 30)),
        "fail_threshold": int(os.environ.get("FAIL_THRESHOLD", 3)),
        "recover_threshold": int(os.environ.get("RECOVER_THRESHOLD", 2)),
        "servers": json.loads(os.environ["SERVERS"]),
    }


@dataclass
class ServerState:
    name: str
    ip: str
    health_url: str
    healthy: bool = True
    dns_record_id: str = ""
    consecutive_failures: int = 0
    consecutive_successes: int = 0


class CloudflareDNS:
    def __init__(self, token: str, zone_id: str):
        self.zone_id = zone_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self.base = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"

    def add(self, name: str, ip: str, ttl: int) -> str:
        r = requests.post(self.base, headers=self.headers,
                          json={"type": "A", "name": name, "content": ip, "ttl": ttl})
        data = r.json()
        if data.get("success"):
            record_id = data["result"]["id"]
            log.info(f"DNS  ADD  {name} → {ip} (id={record_id})")
            return record_id
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


def check_server(server: ServerState, timeout: int = 5) -> bool:
    try:
        r = requests.get(server.health_url, timeout=timeout)
        return r.status_code == 200
    except Exception as e:
        log.debug(f"Health check error for {server.name}: {e}")
        return False


def sync_initial_dns(servers: list[ServerState], dns: CloudflareDNS,
                     record_name: str, ttl: int):
    """On startup, reconcile DNS to match assumed-healthy server list."""
    existing = {r["content"]: r["id"] for r in dns.list_records(record_name)}
    for server in servers:
        if server.ip in existing:
            server.dns_record_id = existing[server.ip]
            log.info(f"DNS existing: {server.name} ({server.ip}) id={server.dns_record_id}")
        else:
            server.dns_record_id = dns.add(record_name, server.ip, ttl)


def run():
    cfg = load_config()
    dns_cfg = cfg["dns"]
    check_interval = cfg["check_interval"]
    fail_threshold = cfg["fail_threshold"]
    recover_threshold = cfg["recover_threshold"]
    record_name = dns_cfg["record_name"]
    ttl = dns_cfg["ttl"]

    servers = [
        ServerState(
            name=s["name"],
            ip=s["ip"],
            health_url=f"http://{s['ip']}:{s.get('health_port', 8080)}/health",
        )
        for s in cfg["servers"]
    ]

    dns = CloudflareDNS(dns_cfg["cf_api_token"], dns_cfg["cf_zone_id"])

    log.info(f"Controller starting — {len(servers)} servers, "
             f"interval={check_interval}s fail={fail_threshold} recover={recover_threshold}")

    sync_initial_dns(servers, dns, record_name, ttl)

    while True:
        for server in servers:
            up = check_server(server)

            if up:
                server.consecutive_failures = 0
                server.consecutive_successes = min(
                    server.consecutive_successes + 1, recover_threshold)

                if not server.healthy and \
                        server.consecutive_successes >= recover_threshold:
                    server.healthy = True
                    server.dns_record_id = dns.add(record_name, server.ip, ttl)
                    log.warning(f"RECOVER {server.name} ({server.ip}) — added to DNS")
            else:
                server.consecutive_successes = 0
                server.consecutive_failures = min(
                    server.consecutive_failures + 1, fail_threshold)

                if server.healthy and \
                        server.consecutive_failures >= fail_threshold:
                    server.healthy = False
                    if server.dns_record_id:
                        dns.remove(server.dns_record_id, server.ip)
                        server.dns_record_id = ""
                    log.warning(f"FAIL    {server.name} ({server.ip}) — removed from DNS")

        healthy_names = [s.name for s in servers if s.healthy]
        log.info(f"Status: healthy={healthy_names}")
        time.sleep(check_interval)


if __name__ == "__main__":
    run()
