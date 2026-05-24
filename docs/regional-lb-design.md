# Regional Load Balancer — Architecture Design

**Branch:** `regional-lb`
**Status:** Deployed — operational as of 2026-05-24

---

## Overview

A lightweight controller per region handles health checking, DNS management, and client provisioning. VPN backends sit behind the controller but carry traffic directly — the controller is never in the data path.

```
Client App
    │  1. provision(device_name, pubkey) → { endpoint, server_pubkey, wg_config }
    │  2. connect via AmneziaWG directly to assigned server
    ▼
Regional Controller  (running on tn2)
    ├── Health checks all backends every 30s via SSH
    ├── Updates regional DNS (only healthy server IPs in A records)
    ├── Tracks per-server active peer count
    └── Provisioning API: assigns client to least-loaded healthy server
    │
    ├── [a1]   10.8.0.0/24  own keypair  awg0:443  (8.222.164.32)
    └── [tn2]  10.8.1.0/24  own keypair  awg0:443  (43.160.238.86)
```

---

## Deployed Configuration

| Component | Host | Details |
|-----------|------|---------|
| Health controller | tn2 | `vpn-controller.service`, `/opt/vpn-controller/health.py` |
| Provisioning API | tn2 | `vpn-provision.service`, binds `127.0.0.1:9000` |
| DNS record | Cloudflare | `nebuchadnezzar.fireshare.uk`, TTL 60s |
| VPN server a1 | 8.222.164.32 | subnet 10.8.0.0/24, awg0 UDP 443 |
| VPN server tn2 | 43.160.238.86 | subnet 10.8.1.0/24, awg0 UDP 443 |

State file: `/etc/vpn-controller/clients.json`
API token: `/etc/vpn-controller/api.token`
Config: `/etc/vpn-controller/controller.yaml`

---

## Components

### 1. Backend VPN Servers

Each server is independent:
- Runs `awg-quick@awg0` on UDP 443
- Has its own keypair (public key registered in controller state)
- Has its own subnet (`10.8.N.0/24`)
- No health sidecar or extra open ports needed — controller checks health via SSH

### 2. Regional Controller

**Health checking (`health.py`):**
- SSHes into each backend every 30s, runs `systemctl is-active awg-quick@awg0`
- After 3 consecutive failures → marks server DOWN, removes its A record from DNS
- After 2 consecutive successes → marks server UP, adds A record back
- On startup: reconciles DNS state with live Cloudflare records
- No extra ports required — uses existing SSH credentials from `controller.yaml`

**Provisioning API (`provision.py`):**
```
POST /provision
Authorization: Bearer <token>

{
  "device_name": "mac1",
  "device_pubkey": "<client-generated-pubkey>",
  "device_privkey": "<client-generated-privkey>",
  "os_type": "macos"   # or "ios" / "android"
}

→ {
  "device_name": "mac1",
  "server_name": "a1",
  "server_pubkey": "<assigned-server-pubkey>",
  "client_ip": "10.8.0.2",
  "endpoint": "nebuchadnezzar.fireshare.uk:443",
  "wg_config": "<complete .conf file contents>"
}
```

The provisioning API:
1. Validates the bearer token
2. Revokes any existing assignment for the device
3. Picks the least-loaded healthy server (SSH health check at request time)
4. Allocates an unused IP from that server's subnet
5. Adds the peer live via `awg set awg0 peer ... allowed-ips .../32`
6. Persists with `awg-quick save awg0`
7. Returns the complete tunnel config

**Load balancing:**
- Strategy: least active peers (most capacity)
- Reprovisioning (calling `/provision` again) revokes the old assignment and re-picks at that moment

### 3. Client Reprovisioning

Admin runs `client/reprovision.sh` on the controller server (tn2):
```bash
# Must run on tn2 where awg tools are installed
./reprovision.sh mac2 macos
# Saves config to ~/Documents/Gen8/mac2.conf
# Transfer to device via AirDrop, scp, or QR code
```

---

## Failover Flow

```
Normal:
  Client ──────────────────────────────▶ a1 (healthy)
                                         keepalive ✓ every 25s

Server failure (a1 goes down):
  ├── Controller detects after 3 SSH probes (~90s)
  ├── Removes a1 IP from DNS
  ├── New connections resolve only to tn2
  └── Clients provisioned on a1 lose their tunnel
        └── Admin reprovisions affected devices → assigned to tn2
            Downtime: ~90s detection + reprovisioning time
```

**Current limitation:** each server has its own keypair, so clients provisioned on a1 cannot transparently reconnect to tn2 — the server pubkeys differ. Manual reprovisioning is required after failover. Automated reprovisioning on server failure is on the roadmap.

---

## Subnet Allocation

| Server | Subnet | Gateway |
|--------|--------|---------|
| a1 | 10.8.0.0/24 | 10.8.0.1 |
| tn2 | 10.8.1.0/24 | 10.8.1.1 |
| (next) | 10.8.2.0/24 | 10.8.2.1 |

Each /24 supports 253 concurrent clients. Servers can be added without touching existing allocations.

---

## Design Decisions

**Why SSH for health checks instead of an HTTP sidecar?**
An HTTP sidecar on port 8080 was the original plan. Cloud firewalls block inter-server traffic on non-standard ports by default, and opening additional ports increases attack surface and maintenance overhead. SSH is already open and reuses existing credentials — zero extra ports.

**Why send the private key to the provisioning API?**
The API returns a complete `.conf` file including the private key so the admin can hand it directly to the device without extra steps. The private key is generated fresh each call, never stored in the state file, and is transmitted only over the local loopback (API binds `127.0.0.1`). For a commercial deployment, the client app would generate the keypair locally, send only the pubkey, and embed its own private key — the server would return a config without a private key field.

---

## Commercial Expansion Notes

- **Authentication**: user tokens issued by a separate auth service (OAuth2 / JWT)
- **Billing**: provisioning API checks entitlement before assigning a server
- **Multi-region**: each region runs an independent controller; Cloudflare GeoDNS routes clients to the nearest region
- **Revocation**: `DELETE /clients/{device_name}` removes the peer immediately from all servers
- **Observability**: add Prometheus metrics (active peers per server, provisioning latency, failover events)
- **Auto-reprovision on failover**: when a server goes down, the controller automatically reprovisions affected clients and notifies them to reload their tunnel config

---

## Implementation Status

- [x] `controller/health.py` — SSH-based health loop, Cloudflare DNS state machine
- [x] `controller/provision.py` — FastAPI provisioning API, live peer management via `awg set`
- [x] `controller/deploy.sh` — install/update script for controller host
- [x] `controller/vpn-controller.service` — systemd unit for health controller
- [x] `controller/vpn-provision.service` — systemd unit for provisioning API
- [x] `client/reprovision.sh` — admin-side provisioning script (run on controller server)
- [x] `server/awg0-server.conf` — server awg0 config template
- [x] `docs/macos-client-setup.md` — end-user import guide
- [ ] Automated reprovisioning on server failure
- [ ] macOS LaunchAgent for keepalive-based automatic failover
- [ ] Multi-region controller deployment
