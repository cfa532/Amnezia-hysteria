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
# Two split lists (see docs/regional-lb-design.md#split-allowedips):
#   reduced "Taobao-direct" list for iOS/Android (< 128 KB, China-app-friendly)
#   honest FULL non-China list for macOS (no config-size limit)
SPLIT_IPS_PATH      = Path("/etc/vpn-controller/split-allowed-ips.txt")        # reduced (mobile)
FULL_SPLIT_IPS_PATH = Path("/etc/vpn-controller/split-allowed-ips-full.txt")   # full (macOS)

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
    user = server.get("ssh_user", "root")
    port = str(server.get("ssh_port", 22))
    base = ["-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
            "-p", port]
    target = f"{user}@{server['ip']}"
    if server.get("ssh_key"):
        return (["ssh", "-i", server["ssh_key"]]
                + base + ["-o", "BatchMode=yes", target])
    elif server.get("ssh_pass"):
        return (["sshpass", "-p", server["ssh_pass"], "ssh"]
                + base + [target])
    raise RuntimeError(f"No SSH credentials for {server['name']}")

def _ssh(server: dict, cmd: str):
    args = _ssh_args(server) + [cmd]
    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"SSH failed on {server['name']}: {r.stderr.strip()}")

def _sudo(server: dict) -> str:
    return "sudo " if server.get("ssh_user", "root") != "root" else ""

AWG_CONF = "/etc/amnezia/amneziawg/awg0.conf"

def ssh_awg_add(server: dict, client_ip: str, pubkey: str):
    """Add peer to running AWG state and persist to conf.

    Root servers: append peer block directly to conf (avoids awg-quick save
    which would corrupt peers that have AllowedIPs=(none) in running state).
    Non-root servers (e.g. minipc/pi): use awg-quick save — direct conf write
    requires root, but awg-quick is NOPASSWD in sudoers. Safe as long as all
    peers in running state have AllowedIPs set, which provisioning ensures.
    """
    s = _sudo(server)
    is_root = server.get("ssh_user", "root") == "root"
    if is_root:
        peer_block = (
            f"\\n[Peer]\\nPublicKey = {pubkey}\\n"
            f"AllowedIPs = {client_ip}/32\\nAdvancedSecurity = on"
        )
        _ssh(server,
             f"awg set awg0 peer {pubkey} allowed-ips {client_ip}/32 advanced-security on && "
             f"grep -qF '{pubkey}' {AWG_CONF} || printf '{peer_block}' >> {AWG_CONF}")
    else:
        _ssh(server,
             f"{s}awg set awg0 peer {pubkey} allowed-ips {client_ip}/32 advanced-security on && "
             f"{s}awg-quick save awg0")

def ssh_awg_remove(server: dict, pubkey: str):
    """Remove peer from running AWG state and persist to conf.

    Root servers: use Python to surgically remove the peer block from conf.
    Non-root servers: remove from running state then awg-quick save (NOPASSWD).
    """
    import base64
    s = _sudo(server)
    is_root = server.get("ssh_user", "root") == "root"
    _ssh(server, f"{s}awg set awg0 peer {pubkey} remove")
    if is_root:
        script = f"""
import pathlib
p = pathlib.Path('{AWG_CONF}')
lines = p.read_text().splitlines(keepends=True)
out, i = [], 0
while i < len(lines):
    if lines[i].strip() == '[Peer]':
        block = [lines[i]]; i += 1
        while i < len(lines) and not lines[i].strip().startswith('['):
            block.append(lines[i]); i += 1
        if not any('{pubkey}' in l for l in block):
            out.extend(block)
    else:
        out.append(lines[i]); i += 1
p.write_text(''.join(out))
"""
        encoded = base64.b64encode(script.encode()).decode()
        _ssh(server, f"echo {encoded} | base64 -d | python3")
    else:
        _ssh(server, f"{s}awg-quick save awg0")


# ── Server selection ───────────────────────────────────────────────────────────

def _provisioned_count(server_name: str, state: dict) -> int:
    """Count active provisioned clients whose preferred server is this one."""
    return sum(1 for c in state["clients"].values()
               if c.get("preferred_server") == server_name and c.get("active"))

def _available_in_region(region: str, cfg: dict, health: dict,
                          state: dict) -> list[dict]:
    """Return config server dicts that are healthy + available in the given region."""
    region_names = set(cfg.get("regions", {}).get(region, {}).get("servers", []))
    result = []
    for s in cfg["servers"]:
        if s["name"] not in region_names:
            continue
        h = health["servers"].get(s["name"], {})
        if h.get("healthy") and h.get("available"):
            result.append({
                **s,
                "_active_peers":     h.get("active_peers", 0),
                "_provisioned":      _provisioned_count(s["name"], state),
            })
    return result

def _least_loaded(servers: list[dict]) -> dict:
    # Primary sort: live active peers (health state, updates every 30s)
    # Tiebreaker: provisioned client count (clients.json, updates immediately)
    return min(servers, key=lambda s: (s["_active_peers"], s["_provisioned"]))


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

def _split_allowed_ips() -> str:
    """Reduced 'Taobao-direct' list for iOS/Android (kept < 128 KB)."""
    if not SPLIT_IPS_PATH.exists():
        raise HTTPException(status_code=500,
                            detail="split-allowed-ips.txt not found on server")
    return _normalize_allowed_ips(SPLIT_IPS_PATH.read_text())

def _full_split_allowed_ips() -> str:
    """Honest full non-China list for macOS (no config-size limit)."""
    if not FULL_SPLIT_IPS_PATH.exists():
        raise HTTPException(status_code=500,
                            detail="split-allowed-ips-full.txt not found on server")
    return _normalize_allowed_ips(FULL_SPLIT_IPS_PATH.read_text())

def _exclude_servers(allowed_csv: str, server_ips: list[str]) -> str:
    """Carve each server's /24 out of a CIDR list, so a client that lands on that
    server doesn't route the server's own IP into the not-yet-established tunnel.
    Needed only for iOS/Android (no route-pinner). macOS doesn't call this."""
    nets = []
    for tok in allowed_csv.replace(",", " ").split():
        tok = tok.strip()
        if not tok:
            continue
        try:
            nets.append(ipaddress.ip_network(tok))
        except ValueError:
            continue
    for ip_str in server_ips:
        try:
            s24 = ipaddress.ip_network(f"{ip_str}/24", strict=False)
        except ValueError:
            continue
        out = []
        for n in nets:
            if n.version != 4:
                out.append(n); continue
            if n.subnet_of(s24):          # entirely inside the server /24 → drop
                continue
            if s24.subnet_of(n):          # server /24 inside this block → split it out
                out.extend(n.address_exclude(s24))
            else:
                out.append(n)             # disjoint → keep
        nets = out
    return ", ".join(str(n) for n in nets)

def _normalize_allowed_ips(raw: str) -> str:
    tokens = []
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.lower().startswith("allowedips"):
            if "=" not in line:
                raise HTTPException(status_code=500,
                                    detail="Invalid split AllowedIPs line: missing '='")
            line = line.split("=", 1)[1].strip()
        line = line.rstrip("\\").strip()
        tokens.extend(part.strip() for part in line.replace(",", " ").split())

    seen = set()
    networks = []
    for token in tokens:
        try:
            network = ipaddress.ip_network(token, strict=True)
        except ValueError as e:
            raise HTTPException(status_code=500,
                                detail=f"Invalid split AllowedIPs entry '{token}': {e}")
        canonical = str(network)
        if canonical not in seen:
            seen.add(canonical)
            networks.append(canonical)

    if not networks:
        raise HTTPException(status_code=500,
                            detail="split-allowed-ips.txt has no valid CIDR entries")
    return ", ".join(networks)

def _normalize_os_type(os_type: str) -> str:
    normalized = os_type.strip().lower()
    if normalized not in {"macos", "ios", "android"}:
        raise HTTPException(status_code=422,
                            detail="os_type must be one of: macos, ios, android")
    return normalized

def _normalize_routing(routing: str) -> str:
    normalized = routing.strip().lower()
    if normalized not in {"full", "split"}:
        raise HTTPException(status_code=422,
                            detail="routing must be one of: full, split")
    return normalized

# All clients connect to AWG directly on UDP 443 (Hysteria2 transport retired).
AWG_DIRECT_ENDPOINT = "nebuchadnezzar.fireshare.uk:443"

def make_wg_config(privkey: str, client_ip: str, server_pubkey: str,
                   os_type: str, routing: str = "split",
                   server_ips: list[str] | None = None) -> str:
    os_type = _normalize_os_type(os_type)
    routing = _normalize_routing(routing)
    server_ips = server_ips or []

    if routing == "full":
        # macOS uses a split-default route to dodge a macOS sendmsg bug with 0.0.0.0/0.
        allowed = ("0.0.0.0/0, ::/0" if os_type in {"ios", "android"}
                   else "0.0.0.0/1, 128.0.0.0/1, ::/1, 8000::/1")
    elif os_type == "macos":
        # Full honest non-China list. No server exclusion: awg-en1-route pins them.
        allowed = _full_split_allowed_ips()
    else:
        # iOS/Android: reduced Taobao-direct list, with server /24s carved out
        # (no route-pinner on mobile, so a covered server IP would loop).
        allowed = _exclude_servers(_split_allowed_ips(), server_ips)

    endpoint = AWG_DIRECT_ENDPOINT

    return (
        f"[Interface]\n"
        f"PrivateKey = {privkey}\n"
        f"Address = {client_ip}/32\n"
        f"DNS = 8.8.8.8, 1.1.1.1\n"
        f"MTU = 1280\n"
        f"{AWG_OBF}\n\n"
        f"[Peer]\n"
        f"PublicKey = {server_pubkey}\n"
        f"Endpoint = {endpoint}\n"
        f"AllowedIPs = {allowed}\n"
        f"PersistentKeepalive = 25\n"
    )

def make_servers_conf(servers: list[dict], hysteria_port: int = 51820) -> str:
    """Generate Hysteria2 servers.conf — same list for all clients in a region."""
    lines = [
        "# Hysteria2 server list — generated by provisioning API",
        "# Format: <ip>  <region>  <port>",
    ]
    for s in servers:
        lines.append(f"{s['ip']:<20} {s.get('region','asia'):<12} {hysteria_port}")
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
    os_type: str = "macos"    # "macos" | "ios" | "android"
    region: str = "asia"
    routing: str = "full"     # "full" (all traffic) | "split" (exclude CN IPs)

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
    os_type = _normalize_os_type(req.os_type)
    routing = _normalize_routing(req.routing)

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
    candidates = _available_in_region(req.region, cfg, health, state)
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
        "device_pubkey":    req.device_pubkey,
        "preferred_server": preferred["name"],
        "client_ip":        client_ip,
        "region":           req.region,
        "os_type":          os_type,
        "routing":          routing,
        "active":           True,
        "provisioned_at":   datetime.utcnow().isoformat(),
    }
    save_state(state)

    wg_config    = make_wg_config(req.device_privkey, client_ip,
                                   shared_pubkey, os_type, routing,
                                   server_ips=[s["ip"] for s in cfg["servers"]])
    servers_conf = make_servers_conf(cfg["servers"])

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
