#!/usr/bin/env python3
"""
VPN provisioning API.

POST /provision   — assign client to least-loaded healthy server, return config
GET  /clients     — list all provisioned clients
DELETE /clients/{device_name} — revoke client (removes peer from server)

State: /etc/vpn-controller/clients.json
"""

import json
import subprocess
import logging
import ipaddress
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import uvicorn
import yaml
import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CONFIG_PATH = Path("/etc/vpn-controller/controller.yaml")
STATE_PATH  = Path("/etc/vpn-controller/clients.json")
API_TOKEN_PATH = Path("/etc/vpn-controller/api.token")

app = FastAPI(title="VPN Provisioning API")


# ── Config & state ────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"clients": {}, "server_keys": {}}

def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, indent=2))

def load_api_token() -> str:
    return API_TOKEN_PATH.read_text().strip() if API_TOKEN_PATH.exists() else ""


# ── Server health ─────────────────────────────────────────────────────────────

def get_healthy_servers(cfg: dict) -> list[dict]:
    healthy = []
    for s in cfg["servers"]:
        try:
            r = requests.get(f"http://{s['ip']}:{s.get('health_port', 8080)}/health",
                             timeout=3)
            if r.status_code == 200:
                healthy.append(s)
        except Exception:
            pass
    return healthy

def peer_count(server_ip: str, state: dict) -> int:
    return sum(1 for c in state["clients"].values()
               if c["server_ip"] == server_ip and c["active"])

def least_loaded(servers: list[dict], state: dict) -> dict:
    return min(servers, key=lambda s: peer_count(s["ip"], state))


# ── IP allocation ─────────────────────────────────────────────────────────────

def allocate_ip(server: dict, state: dict) -> str:
    subnet = ipaddress.IPv4Network(server["subnet"])
    used = {c["client_ip"] for c in state["clients"].values()
            if c["server_ip"] == server["ip"] and c["active"]}
    for host in subnet.hosts():
        ip = str(host)
        if ip == server["gateway"]:
            continue
        if ip not in used:
            return ip
    raise RuntimeError(f"No IPs available on {server['name']}")


# ── awg peer management ───────────────────────────────────────────────────────

def ssh_awg_add(server: dict, client_ip: str, device_pubkey: str):
    cmd = (f"awg set awg0 peer {device_pubkey} allowed-ips {client_ip}/32 && "
           f"awg-quick save awg0")
    _ssh(server, cmd)

def ssh_awg_remove(server: dict, device_pubkey: str):
    cmd = f"awg set awg0 peer {device_pubkey} remove && awg-quick save awg0"
    _ssh(server, cmd)

def _ssh(server: dict, cmd: str):
    if server.get("ssh_key"):
        args = ["ssh", "-i", server["ssh_key"], "-o", "StrictHostKeyChecking=no",
                f"root@{server['ip']}", cmd]
    elif server.get("ssh_pass"):
        args = ["sshpass", "-p", server["ssh_pass"], "ssh",
                "-o", "StrictHostKeyChecking=no", f"root@{server['ip']}", cmd]
    else:
        raise RuntimeError(f"No SSH credentials for {server['name']}")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"SSH failed on {server['name']}: {result.stderr}")


# ── Config generation ─────────────────────────────────────────────────────────

OBF = """
Jc = 4
Jmin = 40
Jmax = 70
S1 = 30
S2 = 40
S3 = 30
S4 = 40
H1 = 11223
H2 = 44556
H3 = 77889
H4 = 99001"""

def make_config(device_privkey: str, client_ip: str, server_pubkey: str,
                endpoint: str, os_type: str) -> str:
    if os_type == "ios":
        allowed = "0.0.0.0/0, ::/0"
    else:
        # macOS split-route: avoids macOS 26.5 sendmsg bug
        allowed = "0.0.0.0/1, 128.0.0.0/1, ::/1, 8000::/1"

    return f"""[Interface]
PrivateKey = {device_privkey}
Address = {client_ip}/32
DNS = 8.8.8.8, 1.1.1.1
MTU = 1280
{OBF}

[Peer]
PublicKey = {server_pubkey}
Endpoint = {endpoint}
AllowedIPs = {allowed}
PersistentKeepalive = 25
"""


# ── API ───────────────────────────────────────────────────────────────────────

class ProvisionRequest(BaseModel):
    device_name: str
    device_pubkey: str
    device_privkey: str   # generated client-side; never stored beyond config gen
    os_type: str = "macos"  # "macos" | "ios" | "android"

class ProvisionResponse(BaseModel):
    device_name: str
    server_name: str
    server_pubkey: str
    client_ip: str
    endpoint: str
    wg_config: str


def _auth(token: Optional[str]):
    expected = load_api_token()
    if expected and token != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/provision", response_model=ProvisionResponse)
def provision(req: ProvisionRequest,
              authorization: Optional[str] = Header(None)):
    _auth(authorization)
    cfg = load_config()
    state = load_state()

    # Revoke existing assignment for this device if any
    if req.device_name in state["clients"]:
        old = state["clients"][req.device_name]
        if old["active"]:
            try:
                server = next(s for s in cfg["servers"]
                              if s["ip"] == old["server_ip"])
                ssh_awg_remove(server, old["device_pubkey"])
            except Exception as e:
                log.warning(f"Could not remove old peer for {req.device_name}: {e}")
        state["clients"][req.device_name]["active"] = False

    healthy = get_healthy_servers(cfg)
    if not healthy:
        raise HTTPException(status_code=503, detail="No healthy servers available")

    server = least_loaded(healthy, state)
    server_pubkey = state["server_keys"].get(server["ip"], "")
    if not server_pubkey:
        raise HTTPException(status_code=500,
                            detail=f"No pubkey registered for {server['name']}")

    client_ip = allocate_ip(server, state)
    endpoint = f"{cfg['dns']['record_name']}:443"

    ssh_awg_add(server, client_ip, req.device_pubkey)

    state["clients"][req.device_name] = {
        "device_pubkey": req.device_pubkey,
        "server_ip": server["ip"],
        "server_name": server["name"],
        "client_ip": client_ip,
        "os_type": req.os_type,
        "active": True,
        "provisioned_at": datetime.utcnow().isoformat(),
    }
    save_state(state)

    wg_config = make_config(req.device_privkey, client_ip, server_pubkey,
                            endpoint, req.os_type)

    log.info(f"Provisioned {req.device_name} → {server['name']} ({client_ip})")
    return ProvisionResponse(
        device_name=req.device_name,
        server_name=server["name"],
        server_pubkey=server_pubkey,
        client_ip=client_ip,
        endpoint=endpoint,
        wg_config=wg_config,
    )


@app.get("/clients")
def list_clients(authorization: Optional[str] = Header(None)):
    _auth(authorization)
    state = load_state()
    return {"clients": state["clients"]}


@app.delete("/clients/{device_name}")
def revoke(device_name: str, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    cfg = load_config()
    state = load_state()

    if device_name not in state["clients"]:
        raise HTTPException(status_code=404, detail="Device not found")

    client = state["clients"][device_name]
    if client["active"]:
        try:
            server = next(s for s in cfg["servers"]
                          if s["ip"] == client["server_ip"])
            ssh_awg_remove(server, client["device_pubkey"])
        except Exception as e:
            log.warning(f"Could not remove peer for {device_name}: {e}")

    state["clients"][device_name]["active"] = False
    save_state(state)
    log.info(f"Revoked {device_name}")
    return {"revoked": device_name}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="info")
