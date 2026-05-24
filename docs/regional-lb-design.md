# Full-Stack VPN — Architecture Design

**Branch:** `full-stack`
**Status:** In progress — 2026-05-24

---

## Overview

Hysteria2 (QUIC/TLS transport) wraps AmneziaWG (obfuscated WireGuard VPN). The controller handles dynamic load balancing, health checking, DNS management, and client provisioning. Neither the controller nor Hysteria2 is in the data path once the tunnel is established.

```
Client
  └─ AmneziaWG (endpoint: 127.0.0.1:1443)
       └─ Hysteria2 (QUIC, masquerades as HTTPS)
              ├─ a1:80  →  awg0:51820
              └─ tn2:80 →  awg0:51820
                   │
              Regional Controller (on tn2)
                   ├── Health checks all servers every 30s via SSH
                   ├── Tracks active_peers per server
                   ├── Updates Cloudflare DNS (healthy servers only)
                   └── Provisioning API: least-loaded server in region
```

---

## Deployed Configuration

| Component | Host | Details |
|-----------|------|---------|
| Health controller | tn2 | `vpn-controller.service`, `/opt/vpn-controller/health.py` |
| Provisioning API | tn2 | `vpn-provision.service`, binds `127.0.0.1:9000` |
| DNS record | Cloudflare | `nebuchadnezzar.fireshare.uk`, TTL 60s |
| VPN + Hysteria2 a1 | 8.222.164.32 | region: asia, awg0 UDP 51820, hysteria2 UDP 80 |
| VPN + Hysteria2 tn2 | 43.160.238.86 | region: asia, awg0 UDP 51820, hysteria2 UDP 80 |

State file: `/etc/vpn-controller/clients.json`
API token: `/etc/vpn-controller/api.token`
Config: `/etc/vpn-controller/controller.yaml`

---

## Key Design Decisions

### Shared AWG keypair across all servers

All servers share one AWG private/public key. This makes failover transparent: the client config has a single server pubkey and a single endpoint (`127.0.0.1:1443`). When Hysteria2 switches servers, the AWG handshake succeeds immediately — every server presents the same identity and every server has every client registered.

Without this, clients are permanently bound to one server's keypair and failover requires reprovisioning.

### Global client IP pool

All clients draw IPs from one subnet (`10.8.0.0/24`) regardless of which server they connect through. Server awg0 interfaces use a reserved block (`10.8.255.x`). This means a client's IP is stable across failover events.

| Block | Purpose |
|-------|---------|
| 10.8.0.0/24 | Client IPs (global pool) |
| 10.8.255.1 | a1 awg0 interface |
| 10.8.255.2 | tn2 awg0 interface |

### Peer registration on all servers

When a client is provisioned, its public key and assigned IP are pushed to every server, not just the assigned one. Failover is transparent. The "assigned server" concept only determines which server the client prefers for new sessions — not where it is allowed to connect.

---

## Server States

Each server has one of three states:

| State | New sessions | Existing sessions | Hysteria2 failover target |
|-------|-------------|------------------|--------------------------|
| Healthy + Available | ✅ | ✅ | ✅ |
| Healthy + At capacity | ❌ | ✅ | ✅ |
| Down | ❌ | ❌ | ❌ |

"At capacity" means `active_peers >= max_peers`. It blocks new provisioning but does not evict existing sessions and remains a valid failover target — a client whose preferred server goes down can still connect here.

---

## Components

### 1. Backend VPN Servers

Each server runs:
- `awg-quick@awg0` on UDP 51820 (internal — reachable only via Hysteria2)
- `hysteria.service` on UDP 80 (public — QUIC, TLS, masquerades as HTTPS)

All servers share the same AWG private key and peer list.

### 2. Regional Controller

**Health checking (`health.py`):**
- SSHes into each server every 30s
- Runs `systemctl is-active awg-quick@awg0` → healthy/down
- Runs `awg show awg0 dump` → counts peers with handshake within 180s → `active_peers`
- Compares `active_peers` to `max_peers` from config → sets `is_available`
- After 3 consecutive failures → marks DOWN, removes A record from DNS
- After 2 consecutive successes → marks UP, adds A record back

**Provisioning API (`provision.py`):**
```
POST /provision
Authorization: Bearer <token>

{
  "device_name": "mac1",
  "device_pubkey": "<client-generated-pubkey>",
  "device_privkey": "<client-generated-privkey>",
  "os_type": "macos",
  "region": "asia"       ← optional, defaults to "asia"
}

→ {
  "device_name": "mac1",
  "server_name": "tn2",
  "server_pubkey": "<shared-awg-pubkey>",
  "client_ip": "10.8.0.3",
  "wg_config": "<complete .conf file contents>",
  "servers_conf": "<hysteria2 servers.conf contents>"
}
```

Provisioning steps:
1. Validate bearer token
2. Revoke any existing assignment for the device (remove peer from all servers)
3. Select region (`request.region` or default)
4. Find candidates: servers in region where `is_healthy AND is_available`
5. If no candidates: raise 503 "region at capacity"
6. Pick least-loaded: `min(candidates, key=lambda s: s.active_peers)`
7. Allocate unused IP from global client pool (`10.8.0.0/24`)
8. Push peer to **all** servers via `awg set awg0 peer ... allowed-ips .../32`
9. Persist with `awg-quick save awg0` on each server
10. Return complete tunnel config + `servers_conf` with preferred server first

**Load balancing:**
- Strategy: least active peers among healthy + available servers in requested region
- "Active peer" = peer with handshake within last 180s (WireGuard session window)
- Session affinity: the preferred server in `servers_conf` is tried first by Hysteria2 failover; the client stays on it as long as it is reachable

### 3. Controller Config (`controller.yaml`)

```yaml
regions:
  asia:
    servers: [a1, tn2]
  # us:
  #   servers: [us1]

servers:
  a1:
    ip: 8.222.164.32
    region: asia
    max_peers: 50
    ssh_key: /etc/vpn-controller/a1-singa.pem
  tn2:
    ip: 43.160.238.86
    region: asia
    max_peers: 50
    ssh_pass: <password>

awg:
  shared_privkey: <shared-awg-private-key>
  shared_pubkey: <shared-awg-public-key>
  client_subnet: 10.8.0.0/24
  server_base_ip: 10.8.255.0
```

### 4. Client Setup

Each client has two configs:

**AmneziaWG (`mac2-hy2.conf`)** — stable, never changes after provisioning:
```ini
[Interface]
PrivateKey = <client-private-key>
Address = 10.8.0.3/32
DNS = 8.8.8.8, 1.1.1.1
MTU = 1280
Jc = 4
Jmin = 40
Jmax = 70
S1 = 30  S2 = 40  S3 = 30  S4 = 40
H1 = 11223  H2 = 44556  H3 = 77889  H4 = 99001

[Peer]
PublicKey = <shared-awg-pubkey>      ← same for every server
Endpoint = 127.0.0.1:1443           ← local Hysteria2 forwarder
AllowedIPs = 0.0.0.0/1, 128.0.0.0/1, ::/1, 8000::/1
PersistentKeepalive = 25
```

**Hysteria2 `servers.conf`** — generated by provisioning, preferred server first:
```
# preferred — assigned by load balancer
43.160.238.86   asia   80
# regional fallback
8.222.164.32    asia   80
```

The Hysteria2 failover script reads this file and tries servers in order. Session affinity is provided naturally: the script only switches when the current server times out.

---

## Failover Flow

```
Normal (session affinity):
  Client ──[Hysteria2]──▶ tn2 (preferred)
                              └─ awg0 (client registered here)

tn2 goes down:
  ├── Hysteria2 failover script detects timeout
  ├── Switches to a1 (next in servers.conf)
  ├── AWG handshake succeeds immediately
  │     └── shared keypair + client already registered on a1
  └── Tunnel restored — no reprovisioning, no admin action
      Downtime: Hysteria2 timeout (~5s) + AWG handshake (~1s)

New session after failover:
  ├── Next POST /provision picks least-loaded healthy server
  ├── May reassign to tn2 once it recovers
  └── Client receives updated servers_conf
```

---

## Region Design (future-proof)

Adding a new region requires only:
1. Deploy Hysteria2 + AWG (shared key) on new server
2. Add server to `controller.yaml` under new region
3. Push all existing client peers to new server

No client config changes. No code changes. Existing clients gain the new server as a cross-region fallback automatically.

---

## Implementation Status

- [x] `controller/health.py` — SSH-based health loop, Cloudflare DNS state machine
- [x] `controller/provision.py` — FastAPI provisioning API, live peer management
- [x] `controller/deploy.sh` — install/update script for controller host
- [x] `controller/vpn-controller.service` — systemd unit for health controller
- [x] `controller/vpn-provision.service` — systemd unit for provisioning API
- [x] `client/reprovision.sh` — admin-side provisioning script
- [x] `server/awg0-server.conf` — server awg0 config template
- [x] `docs/macos-client-setup.md` — end-user import guide
- [ ] Shared AWG keypair deployed to all servers
- [ ] Global client IP pool (`10.8.0.0/24`) replacing per-server subnets
- [ ] `active_peers` tracking in health controller
- [ ] `max_peers` + `is_available` state in health controller
- [ ] Provisioning pushes peers to all servers
- [ ] Per-client `servers_conf` generation with preferred server first
- [ ] Region field in `controller.yaml` and provisioning API
- [ ] All 5 existing clients reprovisioned with new shared-key configs
