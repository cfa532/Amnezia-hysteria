# Regional Load Balancer — Architecture Design

**Branch:** `regional-lb`  
**Status:** Design phase

---

## Overview

A lightweight controller per region handles health checking, DNS management, and client provisioning. VPN backends sit behind the controller but carry traffic directly — the controller is never in the data path.

```
Client App
    │  1. provision(user_token) → { endpoint, server_pubkey, client_ip }
    │  2. connect via AmneziaWG directly to assigned server
    ▼
Regional Controller  (one per region, e.g. ap-controller.vpn.example.com)
    ├── Health checks all backends every 30s
    ├── Updates regional DNS (only healthy server IPs in A records)
    ├── Tracks per-server active peer count
    └── Provisioning API: assigns client to least-loaded healthy server
    │
    ├── [SG-1]  10.8.0.0/24  own keypair  awg0:443
    ├── [SG-2]  10.8.1.0/24  own keypair  awg0:443
    ├── [JP-1]  10.8.2.0/24  own keypair  awg0:443
    └── [JP-2]  10.8.3.0/24  own keypair  awg0:443
```

---

## Components

### 1. Backend VPN Servers

Each server is independent:
- Runs `awg-quick@awg0` on UDP 443
- Has its own keypair (unlike the current shared-key dev setup)
- Has its own subnet (e.g. server N gets `10.8.N.0/24`)
- Exposes a health endpoint (UDP echo or a lightweight HTTP sidecar on loopback)
- Registered in the controller's server inventory

### 2. Regional Controller

A small process (Python or Go) running on a lightweight VPS per region. Responsibilities:

**Health checking:**
- Sends a UDP probe to each backend every 30s
- After 3 consecutive failures → marks server DOWN, removes from DNS
- After 2 consecutive successes → marks server UP, adds back to DNS
- Writes state to a local file for persistence across restarts

**DNS management:**
- Manages an A record for the regional endpoint (e.g. `ap.vpn.example.com`)
- Uses Cloudflare API (or any DNS provider with an API) to add/remove IPs
- TTL: 60s for fast failover

**Provisioning API** (`POST /provision`):
```json
Request:
{
  "user_token": "<auth-token>",
  "device_pubkey": "<client-generated-pubkey>",
  "device_name": "mac1"
}

Response:
{
  "endpoint": "ap.vpn.example.com:443",
  "server_pubkey": "<assigned-server-pubkey>",
  "client_ip": "10.8.2.5",
  "server_ip": "10.8.2.1",
  "obfuscation": {
    "Jc": 4, "Jmin": 40, "Jmax": 70,
    "S1": 30, "S2": 40, "S3": 30, "S4": 40,
    "H1": 11223, "H2": 44556, "H3": 77889, "H4": 99001
  }
}
```
The client app generates its own keypair locally (private key never leaves the device), sends only the public key. The controller picks the least-loaded healthy server, allocates a client IP, adds the peer to that server's `awg0` live config via `awg set`, and returns the connection details.

**Load balancing:**
- Strategy: least active peers (most room)
- Fallback: round-robin among healthy servers
- Sticky sessions: once assigned, a client stays on the same server unless it goes down

### 3. Client Agent (macOS LaunchAgent / iOS App)

Monitors tunnel health and handles failover:
- Watches `PersistentKeepalive` responses (every 25s)
- If no response for 3 intervals (75s) → calls `/provision` again
- Gets new server assignment (may be same or different server)
- Updates tunnel config and reconnects

For managed macOS devices, a lightweight LaunchAgent script handles this. For iOS/Android, it would be integrated into the VPN app.

---

## Failover Flow

```
Normal:
  Client ──────────────────────────────▶ SG-1 (healthy)
                                         keepalive ✓ every 25s

Server failure:
  SG-1 goes down
  ├── Controller detects after 3 probes (~90s)
  ├── Removes SG-1 from DNS
  └── Client keepalives fail after 75s
        ├── Client agent calls /provision
        ├── Gets assigned to SG-2
        └── Reconnects — downtime: ~75–90s total
```

Downtime is bounded by `PersistentKeepalive × 3` on the client side. DNS TTL only matters for new connections; existing sessions detect failure via keepalives.

---

## Subnet Allocation

| Server | Subnet | Controller gateway |
|--------|--------|--------------------|
| SG-1 | 10.8.0.0/24 | 10.8.0.1 |
| SG-2 | 10.8.1.0/24 | 10.8.1.1 |
| JP-1 | 10.8.2.0/24 | 10.8.2.1 |
| JP-2 | 10.8.3.0/24 | 10.8.3.1 |
| ... | 10.8.N.0/24 | 10.8.N.1 |

Each /24 supports 253 concurrent clients per server. Servers can be added without touching existing allocations.

---

## Peer Management

The controller manages peers dynamically via `awg set` (no restart needed):

```bash
# Add peer
awg set awg0 \
  peer <client_pubkey> \
  allowed-ips <client_ip>/32

# Persist to config (for reboot survival)
awg-quick save awg0   # or manual append to awg0.conf

# Remove peer on lease expiry / revocation
awg set awg0 peer <client_pubkey> remove
```

---

## Commercial Expansion Notes

- **Authentication**: user tokens issued by a separate auth service (OAuth2 / JWT). Controller validates token before provisioning.
- **Billing integration**: provisioning API checks entitlement (active subscription, bandwidth quota) before assigning a server.
- **Multi-region**: each region runs an independent controller. A global DNS (e.g. Cloudflare GeoDNS) routes clients to the nearest regional endpoint.
- **Revocation**: controller can immediately remove a peer from all servers when a subscription is cancelled.
- **Observability**: controller exports Prometheus metrics (active peers per server, provisioning latency, failover events).

---

## Implementation Plan

- [ ] `controller/health.py` — UDP probe loop, server state machine
- [ ] `controller/dns.py` — Cloudflare API wrapper (add/remove A records)
- [ ] `controller/provision.py` — assignment logic, `awg set` integration
- [ ] `controller/api.py` — FastAPI HTTP server for provisioning endpoint
- [ ] `client/failover-agent.sh` — macOS LaunchAgent keepalive monitor
- [ ] `client/provision.sh` — calls `/provision`, writes new tunnel config, reloads AmneziaWG
- [ ] `server/setup.sh` — idempotent server bootstrap script
