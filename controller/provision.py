#!/usr/bin/env python3
"""
VPN provisioning API.

POST   /provision              — assign client to least-loaded server in region
GET    /clients                — list all provisioned clients
DELETE /clients/{device_name} — revoke client (removes peer from all servers)

Server health and active_peers are read from /var/run/vpn-health.json,
written every 30s by health.py. No duplicate SSH health checks at provision time.

Peer registration is pushed to ALL servers so that Hysteria2 failover is
transparent — the client works regardless of which server Hysteria2 routes to.
"""

import ipaddress
import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

CONFIG_PATH  = Path("/etc/vpn-controller/controller.yaml")
STATE_PATH   = Path("/etc/vpn-controller/clients.json")
TOKEN_PATH   = Path("/etc/vpn-controller/api.token")
HEALTH_STATE = Path("/var/run/vpn-health.json")

HEALTH_STALE_SECS = 90   # refuse to provision if health data is older than this

app = FastAPI(title="VPN Provisioning API")


# ── Config & state ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"clients": {}}

def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, indent=2))

def load_token() -> str:
    return TOKEN_PATH.read_text().strip() if TOKEN_PATH.exists() else ""

def load_health() -> dict:
    if not HEALTH_STATE.exists():
        raise HTTPException(status_code=503,
                            detail="Health state unavailable — controller not running")
    data = json.loads(HEALTH_STATE.read_text())
    age = time.time() - data.get("updated_at", 0)
    if age > HEALTH_STALE_SECS:
        raise HTTPException(status_code=503,
                            detail=f"Health state stale ({age:.0f}s) — controller may be down")
    return data


# ── SSH helpers ────────────────────────────────────────────────────────────────

def _ssh_args(server: dict) -> list[str]:
    base = ["-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5"]
    if server.get("ssh_key"):
        return (["ssh", "-i", server["ssh_key"]]
                + base + ["-o", "BatchMode=yes", f"root@{server['ip']}"])
    elif server.get("ssh_pass"):
        return (["sshpass", "-p", server["ssh_pass"], "ssh"]
                + base + [f"root@{server['ip']}"])
    raise RuntimeError(f"No SSH credentials for {server['name']}")

def _ssh(server: dict, cmd: str):
    args = _ssh_args(server) + [cmd]
    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"SSH failed on {server['name']}: {r.stderr.strip()}")

def ssh_awg_add(server: dict, client_ip: str, pubkey: str):
    _ssh(server, f"awg set awg0 peer {pubkey} allowed-ips {client_ip}/32 && "
                 f"awg-quick save awg0")

def ssh_awg_remove(server: dict, pubkey: str):
    _ssh(server, f"awg set awg0 peer {pubkey} remove && awg-quick save awg0")


# ── Server selection ───────────────────────────────────────────────────────────

def _available_in_region(region: str, cfg: dict, health: dict) -> list[dict]:
    """Return config server dicts that are healthy + available in the given region."""
    region_names = set(cfg.get("regions", {}).get(region, {}).get("servers", []))
    result = []
    for s in cfg["servers"]:
        if s["name"] not in region_names:
            continue
        h = health["servers"].get(s["name"], {})
        if h.get("healthy") and h.get("available"):
            result.append({**s, "_active_peers": h.get("active_peers", 0)})
    return result

def _least_loaded(servers: list[dict]) -> dict:
    return min(servers, key=lambda s: s["_active_peers"])


# ── IP allocation (global pool) ────────────────────────────────────────────────

def allocate_ip(cfg: dict, state: dict) -> str:
    subnet = ipaddress.IPv4Network(cfg["awg"]["client_subnet"])
    used = {c["client_ip"] for c in state["clients"].values() if c["active"]}
    gateway = str(subnet.network_address + 1)   # 10.8.0.1 reserved
    for host in subnet.hosts():
        ip = str(host)
        if ip == gateway:
            continue
        if ip not in used:
            return ip
    raise RuntimeError("Global client IP pool exhausted")


# ── Config generation ──────────────────────────────────────────────────────────

AWG_OBF = (
    "Jc = 4\nJmin = 40\nJmax = 70\n"
    "S1 = 30\nS2 = 40\nS3 = 30\nS4 = 40\n"
    "H1 = 11223\nH2 = 44556\nH3 = 77889\nH4 = 99001"
)

def make_wg_config(privkey: str, client_ip: str, server_pubkey: str,
                   os_type: str) -> str:
    allowed = ("0.0.0.0/0, ::/0" if os_type == "ios"
               else "0.0.0.0/1, 128.0.0.0/1, ::/1, 8000::/1")
    return (
        f"[Interface]\n"
        f"PrivateKey = {privkey}\n"
        f"Address = {client_ip}/32\n"
        f"DNS = 8.8.8.8, 1.1.1.1\n"
        f"MTU = 1280\n"
        f"{AWG_OBF}\n\n"
        f"[Peer]\n"
        f"PublicKey = {server_pubkey}\n"
        f"Endpoint = 127.0.0.1:1443\n"   # local Hysteria2 UDP forwarder
        f"AllowedIPs = {allowed}\n"
        f"PersistentKeepalive = 25\n"
    )

def make_servers_conf(preferred: dict, all_servers: list[dict],
                      hysteria_port: int = 80) -> str:
    """Generate Hysteria2 servers.conf with preferred server first."""
    lines = [
        "# Hysteria2 server list — generated by provisioning API",
        "# Format: <ip>  <region>  <port>",
        f"{preferred['ip']:<20} {preferred.get('region','asia'):<12} {hysteria_port}",
    ]
    for s in all_servers:
        if s["ip"] != preferred["ip"]:
            lines.append(
                f"{s['ip']:<20} {s.get('region','asia'):<12} {hysteria_port}"
            )
    return "\n".join(lines) + "\n"


# ── Auth ───────────────────────────────────────────────────────────────────────

def _auth(token: Optional[str]):
    expected = load_token()
    if expected and token != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── API models ─────────────────────────────────────────────────────────────────

class ProvisionRequest(BaseModel):
    device_name: str
    device_pubkey: str
    device_privkey: str    # generated client-side; never stored
    os_type: str = "macos" # "macos" | "ios" | "android"
    region: str = "asia"

class ProvisionResponse(BaseModel):
    device_name: str
    server_name: str
    server_pubkey: str
    client_ip: str
    wg_config: str
    servers_conf: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.post("/provision", response_model=ProvisionResponse)
def provision(req: ProvisionRequest,
              authorization: Optional[str] = Header(None)):
    _auth(authorization)
    cfg    = load_config()
    state  = load_state()
    health = load_health()

    # Revoke existing assignment — remove peer from every server
    if req.device_name in state["clients"]:
        old = state["clients"][req.device_name]
        if old["active"]:
            for s in cfg["servers"]:
                try:
                    ssh_awg_remove(s, old["device_pubkey"])
                except Exception as e:
                    log.warning(f"Could not remove old peer from {s['name']}: {e}")
        state["clients"][req.device_name]["active"] = False

    # Select preferred server — least loaded, healthy + available, in region
    candidates = _available_in_region(req.region, cfg, health)
    if not candidates:
        raise HTTPException(status_code=503,
                            detail=f"No servers available in region '{req.region}'")
    preferred = _least_loaded(candidates)

    shared_pubkey = cfg["awg"]["shared_pubkey"]
    client_ip = allocate_ip(cfg, state)

    # Push peer to ALL servers — failover works regardless of which server
    # Hysteria2 routes to
    errors = []
    for s in cfg["servers"]:
        try:
            ssh_awg_add(s, client_ip, req.device_pubkey)
        except Exception as e:
            errors.append(s["name"])
            log.error(f"Failed to add peer to {s['name']}: {e}")
    if errors:
        log.warning(f"Peer push failed on: {errors} — failover to these servers"
                    f" will not work until resolved")

    # Persist
    state["clients"][req.device_name] = {
        "device_pubkey":  req.device_pubkey,
        "preferred_server": preferred["name"],
        "client_ip":      client_ip,
        "region":         req.region,
        "os_type":        req.os_type,
        "active":         True,
        "provisioned_at": datetime.utcnow().isoformat(),
    }
    save_state(state)

    wg_config    = make_wg_config(req.device_privkey, client_ip,
                                   shared_pubkey, req.os_type)
    servers_conf = make_servers_conf(preferred, cfg["servers"])

    log.info(f"Provisioned {req.device_name} → preferred={preferred['name']}"
             f" ip={client_ip} region={req.region}")

    return ProvisionResponse(
        device_name=req.device_name,
        server_name=preferred["name"],
        server_pubkey=shared_pubkey,
        client_ip=client_ip,
        wg_config=wg_config,
        servers_conf=servers_conf,
    )


@app.get("/clients")
def list_clients(authorization: Optional[str] = Header(None)):
    _auth(authorization)
    return {"clients": load_state()["clients"]}


@app.delete("/clients/{device_name}")
def revoke(device_name: str, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    cfg   = load_config()
    state = load_state()

    if device_name not in state["clients"]:
        raise HTTPException(status_code=404, detail="Device not found")

    client = state["clients"][device_name]
    if client["active"]:
        for s in cfg["servers"]:
            try:
                ssh_awg_remove(s, client["device_pubkey"])
            except Exception as e:
                log.warning(f"Could not remove peer from {s['name']}: {e}")

    state["clients"][device_name]["active"] = False
    save_state(state)
    log.info(f"Revoked {device_name}")
    return {"revoked": device_name}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="info")
