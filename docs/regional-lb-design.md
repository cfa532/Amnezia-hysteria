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
- `awg-quick@awg0` on UDP 443 (public — **all** clients connect here directly: iOS, Android, and macOS)
- `hysteria.service` on UDP 51820 — **retired from the client path.** Still installed on the servers but no client uses it; macOS was migrated off the Hysteria2 proxy to direct AWG. See [hysteria-legacy.md](hysteria-legacy.md). (To be removed from the servers later.)

All clients reach AWG directly on UDP 443 via `nebuchadnezzar.fireshare.uk` (DNS round-robin). All servers share the same AWG private key and peer list, so whichever IP DNS hands out, the handshake succeeds against the same identity.

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
6. Pick least-loaded: `min(candidates, key=lambda s: (s.active_peers, s.provisioned_count))`
7. Allocate unused IP from global client pool (`10.8.1.0/24`)
8. Push peer to **all** servers via `awg set awg0 peer ... allowed-ips .../32 advanced-security on`
9. Persist conf: root servers append peer block directly to `awg0.conf`; non-root servers run `awg-quick save awg0` (NOPASSWD in sudoers)
10. Return complete tunnel config + `servers_conf`

**Load balancing:**
- Strategy: least active peers among healthy + available servers in requested region
- "Active peer" = peer with handshake within last 180s (WireGuard session window)
- Session affinity: the preferred server in `servers_conf` is tried first by Hysteria2 failover; the client stays on it as long as it is reachable

### 3. Controller Config (`controller.yaml`)

```yaml
regions:
  asia:
    servers: [tn1, minipc]

servers:
  tn1:
    ip: 43.165.128.251
    region: tokyo
    max_peers: 50
    ssh_pass: <password>
  minipc:
    ip: 125.229.161.122
    region: taiwan
    max_peers: 50
    ssh_user: pi
    ssh_port: 220
    ssh_key: /etc/vpn-controller/minipc-key

awg:
  shared_privkey: <shared-awg-private-key>
  shared_pubkey: <shared-awg-public-key>
  client_subnet: 10.8.1.0/24   # 10.8.0.0/24 is reserved for minipc platform users
```

**minipc sudoers** (`/etc/sudoers.d/vpn-controller`):
```
pi ALL=(ALL) NOPASSWD: /usr/bin/awg, /usr/bin/awg-quick
```
This allows the provisioning API to push peers to minipc without a password. `awg-quick save awg0` is used instead of direct conf editing (pi cannot write `/etc/amnezia/amneziawg/awg0.conf` directly).

### 4. Client Setup

**macOS** — a single AmneziaWG config plus the `awg-en1-route` route-pinner daemon.

**AmneziaWG (`mac1.conf`)** — stable, never changes after provisioning:
```ini
[Interface]
PrivateKey = <client-private-key>
Address = 10.8.1.2/32
DNS = 8.8.8.8, 1.1.1.1
MTU = 1280
Jc = 4  Jmin = 40  Jmax = 70
S1 = 30  S2 = 40  S3 = 30  S4 = 40
H1 = 11223  H2 = 44556  H3 = 77889  H4 = 99001

[Peer]
PublicKey = <shared-awg-pubkey>             ← same for every server
Endpoint = nebuchadnezzar.fireshare.uk:443  ← DNS round-robin to tn1/minipc
AllowedIPs = <honest FULL non-China list (~11975 routes) — NOT the reduced split-allowed-ips.txt, which is the mobile list>
PersistentKeepalive = 25
```

macOS does not need server IP exclusions baked into AllowedIPs. The `awg-en1-route`
LaunchDaemon resolves the endpoint hostname and pins each returned A record to the
en1 gateway, taking precedence over any matching AllowedIPs CIDR. So even though
minipc (125.229.161.122) falls inside the covered block `125.224.0.0/12`, the
tunnel works — and because the daemon reads DNS, no per-server config is baked into
the client.

> macOS formerly tunnelled AWG over a local Hysteria2 proxy (`Endpoint = 127.0.0.1:1443`
> plus a `servers.conf`). That was retired — Hysteria tripled packet loss on the
> cross-strait path. See [hysteria-legacy.md](hysteria-legacy.md).

**iOS / Android** — single conf, connects directly to AWG on port 443 (no route-pinner — mobile can't run one, so server IPs must be excluded from AllowedIPs):
```ini
[Interface]
PrivateKey = <client-private-key>
Address = 10.8.1.4/32
DNS = 8.8.8.8, 1.1.1.1
MTU = 1280
...obfuscation params...

[Peer]
PublicKey = <shared-awg-pubkey>
Endpoint = nebuchadnezzar.fireshare.uk:443   ← DNS round-robin to tn1/minipc
AllowedIPs = <split-tunnel china CIDRs>
PersistentKeepalive = 25
```

---

## Failover Flow

All clients (macOS, iOS, Android) fail over the same way — via DNS.

```
Normal:
  Client ──AWG / UDP 443──▶ nebuchadnezzar.fireshare.uk  (DNS round-robin: tn1 or minipc)
                                └─ awg0 (peer registered on both servers, shared keypair)

One server goes down:
  ├── health.py detects failure (3 consecutive SSH checks)
  ├── Removes the dead server's A record from nebuchadnezzar.fireshare.uk (TTL 60s)
  ├── Client re-resolves on its next handshake → lands on the surviving server
  └── AWG handshake succeeds immediately (shared keypair, peer on both servers)
      Tunnel restored — no reprovisioning, no admin action

macOS: the awg-en1-route daemon re-pins whatever IP DNS now returns, so the new
endpoint still egresses via en1.
```

---

## Region Design (future-proof)

Adding a new region requires only:
1. Deploy Hysteria2 + AWG (shared key) on new server
2. Add server to `controller.yaml` under new region
3. Push all existing client peers to new server

No client config changes. No code changes. Existing clients gain the new server as a cross-region fallback automatically.

---

## Operations How-To

### Add a server

1. Deploy AWG + Hysteria2 on the new host (use `server/awg0-server.conf` as template). Copy the **shared** AWG private key — do not generate a new one.
2. Add the server to `/etc/vpn-controller/controller.yaml` under the appropriate region:
   ```yaml
   servers:
     newserver:
       ip: <ip>
       region: asia
       max_peers: 50
       ssh_user: root        # omit if root
       ssh_port: 22          # omit if 22
       ssh_key: /etc/vpn-controller/newserver-key   # or ssh_pass
   regions:
     asia:
       servers: [tn1, minipc, newserver]
   ```
3. Add a DNS A record for `nebuchadnezzar.fireshare.uk` pointing to the new server IP (Cloudflare dashboard).
4. Push all existing client peers to the new server — re-run provisioning for each client **or** manually sync the peer list:
   ```bash
   # On tn1: copy peers from an existing server to the new one
   awg showconf awg0 | grep -A3 "\[Peer\]" | \
     ssh root@<newserver-ip> "awg addconf awg0 /dev/stdin"
   ```
5. Restart health controller to pick up the config change:
   ```bash
   systemctl restart vpn-controller
   ```
6. For non-root servers (like minipc), ensure sudoers allows AWG without a password:
   ```
   <user> ALL=(ALL) NOPASSWD: /usr/bin/awg, /usr/bin/awg-quick
   ```

**macOS clients pick up the new server automatically** once its A record is added — the `awg-en1-route` daemon re-resolves the endpoint hostname and pins the new IP; no reprovision needed. **iOS/Android** also fail over via DNS, but if the new server's IP falls inside their AllowedIPs split list it must be excluded first (they have no route-pinner) — see [Known Design Limitations](#-iosandroid-routing-loop-when-server-ip-falls-inside-allowedips).

---

### Remove a server

1. Remove its entry from `controller.yaml` (both `servers:` and `regions:`).
2. Remove its DNS A record from Cloudflare.
3. Restart health controller: `systemctl restart vpn-controller`.
4. Clients already have the server in their `servers.conf` — they will try it and fail until the session expires, then fall back to a surviving server. To update immediately, reprovision clients.

---

### Add a user / device

Run `reprovision.sh` on tn1:
```bash
export PROVISION_TOKEN=$(cat /etc/vpn-controller/api.token)
bash /opt/vpn-controller/reprovision.sh <device_name> <os_type> <routing> /tmp/output
# os_type: macos | ios | android
# routing: full | split
```
Output files are in `/tmp/output/`:
- `<device_name>.conf` — AWG config to import on the device (all platforms)
- `servers.conf` — legacy Hysteria2 server list; only relevant if running the
  retired Hysteria proxy ([hysteria-legacy.md](hysteria-legacy.md)). Current macOS
  clients ignore it — the `awg-en1-route` daemon reads DNS instead.

QR code for mobile:
```bash
qrencode -t ansiutf8 < /tmp/output/<device_name>.conf
```

---

### Remove a user / device

```bash
curl -s -X DELETE http://127.0.0.1:9000/clients/<device_name> \
     -H "Authorization: Bearer $(cat /etc/vpn-controller/api.token)"
```
This removes the peer from all servers immediately. The client will lose connectivity on next handshake attempt.

---

### Change Hysteria2 port

The Hysteria2 port (default 51820) is set in two places:

1. **Server**: `/etc/hysteria/config.yaml` on each server — change `listen: :51820` and restart `hysteria.service`.
2. **clients' `servers.conf`**: the port is written at provisioning time by `make_servers_conf()` in `provision.py`. Reprovision all clients after changing the port, or manually update their `servers.conf`.

The AWG port (443) is separate and unaffected.

---

## Split AllowedIPs

### Two-tier CIDR lists

Split-tunnel routing uses **two different-sized AllowedIPs lists** because iOS/Android
have a config-size limit that macOS does not (`docs/setup.md`): iOS showed unreliable
handshakes with the honest full list (~198 KB, **11975 routes**); a reduced list under
128 KB connects reliably.

| Platform | List | Character | Server IP exclusions needed? |
|----------|------|-----------|------------------------------|
| **macOS** | honest **full** split list (~11975 routes, ~198 KB) | full non-China coverage | No — `awg-en1-route` daemon pins server IPs |
| **iOS / Android** | **reduced "Taobao-direct"** split list (~7798 routes, < 128 KB) + per-server CIDR splits | size-limited *and* China-app-friendly | Yes — no route-pinner possible on mobile |

**macOS** uses the full list — the honest non-China complement (~11975 routes). macOS
has no config-size limit, so it keeps full coverage. Server IPs do **not** need to be
excluded: the `awg-en1-route` daemon resolves the endpoint hostname and installs `/32`
host routes via en1 that take precedence over any matching AllowedIPs CIDR.

**iOS/Android** use the reduced **Taobao-direct** list (`/etc/vpn-controller/split-allowed-ips.txt`
on tn1, ~7798 routes). It is tailored two ways: (1) kept under 128 KB so the conf imports
reliably via QR — iOS handshakes were unreliable with the 198 KB full list; and (2)
curated so Taobao and other major Chinese apps route **direct** (outside the tunnel),
keeping them fast and avoiding breakage. Because mobile cannot run a route-pinner, any
server IP inside a covered CIDR must also be excluded with a per-server CIDR split
before the conf is distributed.

> **Do not confuse the two.** `split-allowed-ips.txt` is the *reduced (mobile)* list.
> Applying it to a Mac silently downgrades that Mac's coverage. The full macOS list is
> larger and is the honest non-China complement (generated via the
> [procustodibus AllowedIPs calculator](https://www.procustodibus.com/blog/2021/03/wireguard-allowedips-calculator/)).

### Current server IP coverage

| Server | IP | In AllowedIPs? | Action required |
|--------|----|----------------|-----------------|
| tn1 | 43.165.128.251 | No — excluded via CIDR split of `43.160.0.0/12` | None |
| minipc | 125.229.161.122 | **Yes** — covered by `125.224.0.0/12` | iOS/Android confs need CIDR split for `125.229.161.0/24` |

macOS clients are unaffected. iOS/Android clients connecting to minipc as their active server will experience a routing loop and handshake failure until `125.229.161.0/24` is split out of `125.224.0.0/12` in the iOS conf files.

### Updating the split lists

The **reduced (mobile)** list lives at `/etc/vpn-controller/split-allowed-ips.txt` on
tn1 (served by `provision.py`). The **full (macOS)** list is the honest non-China
complement (~11975 routes); regenerate it with the procustodibus calculator from the
current China CIDR set. **Do not** copy the reduced list onto a Mac.

```bash
# macOS: put the FULL list (not split-allowed-ips.txt) into mac1.conf / mac2.conf
#        AllowedIPs, then re-import in the AmneziaWG app.
# Mobile: update split-allowed-ips.txt (keep it < 128 KB), re-run the per-server
#         CIDR split, and redistribute QR codes.
```

### Adding a new server — AllowedIPs checklist

1. Check if the new server IP falls inside the current AllowedIPs:
   ```python
   python3 -c "
   import ipaddress, pathlib
   ip = ipaddress.ip_address('<new-server-ip>')
   raw = pathlib.Path('split-allowed-ips.txt').read_text()
   cidrs = [ipaddress.ip_network(t.strip()) for t in raw.replace(',', ' ').split() if t.strip()]
   match = next((c for c in cidrs if ip in c), None)
   print(match or 'safe — not in AllowedIPs')
   "
   ```
2. If the IP is safe (not matched): no AllowedIPs changes needed.
3. If the IP is matched: split the covering CIDR to exclude the server's `/24` from the iOS/Android conf files and redistribute to all mobile users.
4. macOS needs nothing — the `awg-en1-route` daemon resolves the endpoint hostname and pins whatever IP DNS returns, regardless of AllowedIPs coverage.

---

## Implementation Status

- [x] `controller/health.py` — SSH-based health loop, Cloudflare DNS state machine, active_peers + availability tracking
- [x] `controller/provision.py` — FastAPI provisioning API, multi-server peer push, servers_conf generation; avoids `awg-quick save` corruption; handles root vs non-root servers
- [x] `controller/deploy.sh` — install/update script for controller host
- [x] `controller/vpn-controller.service` — systemd unit for health controller (running on tn1)
- [x] `controller/vpn-provision.service` — systemd unit for provisioning API (running on tn1, port 9000)
- [x] `client/reprovision.sh` — admin-side provisioning script (outputs wg_config; servers.conf is legacy)
- [x] `client/awg-en1-route.sh` — macOS route-pinner: resolves the endpoint hostname and pins each A record to en1 (replaces the retired `hysteria-udp-proxy.py`)
- [x] `server/awg0-server.conf` — server awg0 config template
- [x] `docs/macos-client-setup.md` — end-user import guide
- [x] Shared AWG keypair deployed to tn1 and minipc
- [x] Client IP pool: `10.8.1.0/24`; `10.8.0.0/24` reserved for minipc platform users
- [x] Provisioning pushes peers to all servers (failover transparent)
- [x] Region "asia" covering tn1 (Tokyo) + minipc (Taiwan)
- [x] minipc sudoers: `pi NOPASSWD: /usr/bin/awg, /usr/bin/awg-quick`
- [x] All clients provisioned and tested: mac1 (10.8.1.2), mac2 (10.8.1.3), ios1–3 (10.8.1.4–6), android1–3 (10.8.1.7–9)
