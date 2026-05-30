# Full-Stack VPN — Architecture Design

**Branch:** `full-stack`
**Status:** Updated — 2026-05-30

---

## Overview

Hysteria2 (QUIC/TLS transport) wraps AmneziaWG (obfuscated WireGuard VPN). The controller handles dynamic load balancing, health checking, DNS management, and client provisioning. Neither the controller nor Hysteria2 is in the data path once the tunnel is established.

```
Client
  └─ AmneziaWG (endpoint: 127.0.0.1:1443)
       └─ Hysteria2 (QUIC, masquerades as HTTPS)
              └─ tn1:51820  or  minipc:51820  →  awg0:443
                        │              │
              nebuchadnezzar.fireshare.uk (Cloudflare DNS round-robin, TTL 60s)
```

---

## Deployed Configuration

| Component | Host | Details |
|-----------|------|---------|
| DNS record | Cloudflare | `nebuchadnezzar.fireshare.uk`, TTL 60s, two A records (round-robin) |
| VPN + Hysteria2 tn1 | 43.165.128.251 | region: tokyo, awg0 UDP 443, hysteria2 UDP 51820 |
| VPN + Hysteria2 minipc | 125.229.161.122 | region: taiwan, awg0 UDP 443, hysteria2 UDP 51820 |
| Health controller | tn1 (43.165.128.251) | `/opt/vpn-controller/health.py`, systemd `vpn-controller.service` |

**Decommissioned:** a1 (8.222.164.32, Singapore), tn2 (43.160.238.86, Singapore) — services stopped 2026-05-30

**minipc routing requirements** (non-obvious, will break silent if missing):
- `iptables -t nat -A POSTROUTING -s 10.8.1.0/24 -o enp3s0 -j MASQUERADE` — NAT for tn1 subnet clients
- `ip route add 10.8.1.0/24 dev awg0` — return path for tn1 clients (minipc's awg0 is 10.8.0.1/24, no auto-route for 10.8.1.x)
- Both are in `/etc/amnezia/amneziawg/awg0.conf` PostUp/PostDown

---

## Known Design Limitations

### ⚠ iOS/Android routing loop when server IP falls inside AllowedIPs

**Severity: High. Affects every mobile client whenever a new server is added.**

iOS and Android cannot run a route fix daemon. This means the only mechanism available to prevent a routing loop is the AllowedIPs list itself — the server IP must not appear in any covered CIDR.

**How the loop occurs:**

Mobile clients use split-tunnel routing. AllowedIPs contains the full China IP list, which includes Alibaba Cloud and Tencent Cloud ranges (e.g., `43.160.0.0/12`). Any server provisioned on one of these providers (Tokyo, Singapore, HK nodes on Alibaba/Tencent) will likely land inside a covered block. When the client tries to reach the server to establish a handshake, the OS routes those packets into the not-yet-established tunnel — the handshake can never complete.

**macOS is not affected** — the route fix LaunchDaemon adds a static host route for the server IP via the physical interface, taking precedence over AWG routes. This is not possible on iOS/Android.

**Current workaround:** Split the covering CIDR into sub-CIDRs that collectively exclude the server's `/24`. For example, `43.160.0.0/12` was split into 12 CIDRs to exclude `43.165.128.0/24` (tn1's block). This works but has a serious operational cost:

- Every new server added in an Alibaba/Tencent IP range requires a new CIDR split.
- Updated conf files must be redistributed to every mobile user (new QR code scan).
- The AllowedIPs line grows with each exclusion, making conf files increasingly fragile.
- If a server IP changes within the same /24 the exclusion still holds; if it moves to a new /24 within a covered block, the split must be redone.

**Structural root cause:** AllowedIPs is static and baked into the client conf at provisioning time. There is no mechanism on iOS/Android to dynamically exclude new server IPs at runtime.

**Recommended mitigations (in order of preference):**

1. **Choose server IPs outside the AllowedIPs coverage** — Prefer cloud providers whose IP allocations are not in the China routing list. AWS Tokyo (`13.x`, `52.x`, `54.x`), Vultr, Linode (`139.x`), Hetzner are typically safe. Avoid Alibaba Cloud and Tencent Cloud if the resulting IP will land in a covered block. Verify before deploying: check the candidate IP against the AllowedIPs list.

2. **Add a stable relay node with a guaranteed-safe IP** — One cheap VPS (US/EU IP, provably outside AllowedIPs) acts as the permanent iOS/Android endpoint. iOS clients always connect to the relay; the relay forwards to the actual backend. Backend IPs can change freely without touching any client conf. Adds ~20–50 ms latency but eliminates the redistribution problem entirely.

3. **Accept redistribution on server changes** — Keep the current split approach. Treat server IP changes as a client migration event that requires a QR code redistribution to all mobile users. Acceptable only if server changes are rare (< once a year) and the user base is small.

**When adding any new server:** Before deploying, run the candidate IP against the AllowedIPs list. If it falls inside a covered CIDR, either reject the IP and request a different one from the provider, or execute a CIDR split and plan for a full mobile conf redistribution.

---

## Key Design Decisions

### Shared AWG keypair across all servers

All servers share one AWG private/public key. This makes failover transparent: the client config has a single server pubkey and a single endpoint (`127.0.0.1:1443`). When Hysteria2 switches servers, the AWG handshake succeeds immediately — every server presents the same identity and every server has every client registered.

Without this, clients are permanently bound to one server's keypair and failover requires reprovisioning.

### Global client IP pool

Clients draw IPs from per-network subnets. Server awg0 interfaces use `.1` of their respective subnet.

| Block | Purpose |
|-------|---------|
| 10.8.0.0/24 | minipc (Taiwan) clients — managed by external platform |
| 10.8.1.0/24 | tn1 (Tokyo) clients — assigned at provisioning |
| 10.8.1.1 | tn1 awg0 interface |

**Why two subnets:** minipc owns `10.8.0.0/24`; tn1 clients were moved to `10.8.1.0/24` to avoid IP conflicts. Since both servers share the same AWG keypair, tn1 clients are registered as peers on minipc (10.8.1.x/32 AllowedIPs), enabling one-way failover: tn1 clients can connect to minipc, but minipc clients cannot connect to tn1.

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
- `awg-quick@awg0` on UDP 443 (public for iOS/Android direct; internal for macOS via Hysteria2 loopback)
- `hysteria.service` on UDP 51820 (public — QUIC, TLS, masquerades as HTTPS)

Both servers share the same Hysteria2 port (51820) so DNS round-robin is transparent to macOS clients. The `hysteria-udp-proxy.py` reads `servers.conf` and randomly selects a server on each new session.

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
  "client_ip": "10.8.1.3",
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
7. Allocate unused IP from region client pool (`10.8.1.0/24` for tokyo/tn1)
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
  client_subnet: 10.8.1.0/24   # tn1 clients; 10.8.0.0/24 is minipc
  server_base_ip: 10.8.255.0
```

### 4. Client Setup

Each client has two configs:

**AmneziaWG (`mac2-hy2.conf`)** — stable, never changes after provisioning:
```ini
[Interface]
PrivateKey = <client-private-key>
Address = 10.8.1.3/32
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

- [x] `controller/health.py` — SSH-based health loop, Cloudflare DNS state machine (supports ssh_user/ssh_port per server)
- [x] `controller/provision.py` — FastAPI provisioning API, live peer management
- [x] `controller/deploy.sh` — install/update script for controller host
- [x] `controller/vpn-controller.service` — systemd unit for health controller
- [x] `controller/vpn-provision.service` — systemd unit for provisioning API
- [x] `client/reprovision.sh` — admin-side provisioning script
- [x] `server/awg0-server.conf` — server awg0 config template
- [x] `docs/macos-client-setup.md` — end-user import guide
- [ ] Shared AWG keypair deployed to all servers
- [x] Client IP pools: `10.8.1.0/24` (tn1/tokyo), `10.8.0.0/24` reserved for minipc
- [ ] `active_peers` tracking in health controller
- [ ] `max_peers` + `is_available` state in health controller
- [ ] Provisioning pushes peers to all servers
- [ ] Per-client `servers_conf` generation with preferred server first
- [ ] Region field in `controller.yaml` and provisioning API
- [ ] All 5 existing clients reprovisioned with new shared-key configs
