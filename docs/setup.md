# Morphous VPN — Setup Documentation

**Date:** 2026-05-23  
**Author:** user  

---

## Overview

Morphous VPN is a censorship-resistant VPN stack designed to bypass GFW throttling of TCP connections from China to overseas servers. The architecture combines two layers:

1. **AmneziaWG** — a WireGuard fork with obfuscation (junk packets, size randomisation) to prevent DPI fingerprinting
2. **Hysteria2** — a QUIC/UDP-based proxy that bypasses GFW's TCP throttling entirely

### Why Hysteria2?

GFW aggressively throttles TCP connections to Alibaba Cloud Singapore (<SERVER_1_IP>), limiting throughput to ~26 KB/s (a 20 MB SCP transfer takes 12+ minutes). QUIC (UDP) is not throttled on this path. Hysteria2 tunnels AmneziaWG's UDP packets over QUIC, achieving 750 KB/s–3.9 MB/s — a 30–150× improvement over the previous wstunnel (WebSocket/TCP) transport.

### Previous Setup (wstunnel)

AmneziaWG → wstunnel WebSocket → TCP → server. Throttled to ~8.5 KB/s by GFW.

---

## Architecture

```
[Client: Mac]
  App traffic
    ↓
  AmneziaWG (utun, obfuscated WireGuard)
    ↓ UDP to 127.0.0.1:1443 (Sequoia) or 127.0.0.1:1444 (Tahoe)
  Hysteria2 client (QUIC/UDP)
    ↓ QUIC over UDP port 443 → ISP → internet
  Hysteria2 server (a1, <SERVER_1_IP>:443)
    ↓ UDP forwarded to 127.0.0.1:51820
  AmneziaWG server (awg0, port 51820)
    ↓
  Internet (NAT via eth0)
```

---

## Servers

### a1 — Alibaba Cloud Singapore

| Property | Value |
|----------|-------|
| IP | <SERVER_1_IP> |
| OS | Ubuntu (systemd) |
| SSH | `ssh -i ~/.ssh/your-key.pem root@<SERVER_1_IP>` |

### tn2 — Second Server

| Property | Value |
|----------|-------|
| IP | <SERVER_2_IP> |
| OS | Ubuntu (systemd) |
| SSH | `ssh -i ~/.ssh/your-key.pem root@<SERVER_2_IP>` |

**Services:**
- **Hysteria2**: UDP/443, config `/etc/hysteria/server.yaml`, `systemctl status hysteria`
- **AmneziaWG**: UDP/51820, config `/etc/amnezia/amneziawg/wg0.conf`, `systemctl status awg-quick@wg0`
- **wstunnel**: TCP/443 (legacy WebSocket transport, kept for backward compatibility), `systemctl status wstunnel`

**Registered peers on tn2:**

| Device | Public Key | VPN IP |
|--------|-----------|--------|
| Sequoia (device1) | `gXM4mZQBlX9/wF+X4I+DhhAkwTgnVZV/sP58AhUOsjA=` | 10.8.0.2 |
| Tahoe (device2) | `Ls4WuxfPXoMWNlNsKyoflzRGB+FhstL/l260fW7f8GI=` | 10.8.0.3 |
| (spare) | `CiaYmUfzbj/8Rd5SrEpkULclZHGnyq9o2LShO1c0hU4=` | 10.8.0.4 |
| (spare) | `eHIU8XgQgnL1Pt1SRgo7RK2QM/oJ0LJW4yy1Dimw4EU=` | 10.8.0.5 |

To switch a client to tn2, change `server:` in `client.yaml` to `<SERVER_2_IP>:443`. The AmneziaWG profiles are unchanged (same server public key, same local Hysteria2 endpoints).

#### Hysteria2 Server

- Binary: `/usr/local/bin/hysteria`
- Config: `/etc/hysteria/server.yaml` (port 443) and `server-4443.yaml` (port 4443, for testing only)
- Service: `systemctl status hysteria` and `hysteria-4443`

**`/etc/hysteria/server.yaml`:**
```yaml
listen: :443

tls:
  cert: /etc/ssl/<your-domain>/fullchain.pem
  key: /etc/ssl/<your-domain>/key.pem

auth:
  type: password
  password: "<YOUR_AUTH_PASSWORD>"

masquerade:
  type: proxy
  proxy:
    url: https://www.bing.com/
    rewriteHost: true
```

The masquerade makes Hysteria2 look like an HTTPS server to passive observers — QUIC connections that don't authenticate get a valid Bing response.

#### AmneziaWG Server

- Interface: `wg0` (awg0)
- Config: `/etc/wireguard/awg0.conf`
- Listen port: **UDP 51820** (moved from 443 to avoid conflict with Hysteria2)
- VPN subnet: `10.8.0.0/24`
- Server VPN IP: `10.8.0.1`

**Peers registered on server:**

| Device | Public Key | VPN IP |
|--------|-----------|--------|
| Sequoia (device1) | `gXM4mZQBlX9/wF+X4I+DhhAkwTgnVZV/sP58AhUOsjA=` | 10.8.0.2 |
| Tahoe (device2) | `Ls4WuxfPXoMWNlNsKyoflzRGB+FhstL/l260fW7f8GI=` | 10.8.0.3 |

**Server public key:** `5LIkWD1IpDRYgMesUUbatwofnsn8AVK2p3cFfgANJyA=`

**IP forwarding and NAT** are enabled:
```bash
sysctl net.ipv4.ip_forward = 1
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
iptables -A FORWARD -i wg0 -j ACCEPT
```

---

## Client: Sequoia (device1)

### Network Interfaces

| Interface | Network | Gateway | Notes |
|-----------|---------|---------|-------|
| en0 | 192.168.99.x | 192.168.99.1 | Wired, goes through soft router VPN |
| en1 | 192.168.10.x | 192.168.10.1 | WiFi, direct home router (no VPN) |

### Hysteria2 Client

- Binary: `~/bin/hysteria` (arm64)
- Config: `~/Library/Application Support/hysteria/client.yaml`
- LaunchAgent: `~/Library/LaunchAgents/uk.fireshare.hysteria.plist` (auto-start, auto-restart)
- Log: `/tmp/hysteria-mac.log`

**`client.yaml`:**
```yaml
server: <SERVER_1_IP>:443

auth: <YOUR_AUTH_PASSWORD>

tls:
  sni: <YOUR_HOSTNAME>
  insecure: false

transport:
  udp:
    hopInterval: 0s

udpForwarding:
  - listen: 127.0.0.1:1443
    remote: 127.0.0.1:51820
    timeout: 0s
```

Hysteria2 listens on `127.0.0.1:1443` and forwards all UDP to the server's AmneziaWG port (51820 on 127.0.0.1 relative to the server).

### AmneziaWG Client

- App: AmneziaWG (macOS)
- Active profile: **`device1-wstunnel`** (Endpoint = `127.0.0.1:1443`)
- Config file: `~/Documents/Gen8/device1-wstunnel.conf`
- VPN IP: `10.8.0.2`

**Key AmneziaWG parameters:**
```ini
[Interface]
PrivateKey = <DEVICE1_PRIVATE_KEY>
Address = 10.8.0.2/32
DNS = 10.8.0.1
MTU = 1280
Jc = 4
Jmin = 40 / Jmax = 70      # Junk packet count and size range
S1 = 30 / S2 = 40          # Init packet size modifications
S3 = 30 / S4 = 40
H1-H4 = 11223/44556/77889/99001  # Header magic values

[Peer]
PublicKey = 5LIkWD1IpDRYgMesUUbatwofnsn8AVK2p3cFfgANJyA=
Endpoint = 127.0.0.1:1443
AllowedIPs = <all IPs except server IPs — see conf file>
PersistentKeepalive = 25
```

The `AllowedIPs` is a split-tunnel list covering all IPs **except** `<SERVER_1_IP>` and `<SERVER_2_IP>` (server IPs), which are excluded so Hysteria2 traffic never loops back into the VPN.

### Routing Fix (Critical)

**Problem:** When AmneziaWG activates, macOS adds a spurious host route `<SERVER_1_IP> → utun` even though that IP is excluded from AllowedIPs. This causes Hysteria2's QUIC packets to loop into the VPN tunnel.

**Fix:** A static host route via the WiFi gateway takes precedence over the cloned utun route:
```bash
sudo route delete -host <SERVER_1_IP> 2>/dev/null
sudo route add -host <SERVER_1_IP> 192.168.10.1
```

**Persistence:** A LaunchDaemon runs as root using `route monitor` for reactive fixing — corrects the route immediately when AmneziaWG changes it, with no polling delay.

Script (`~/bin/fix-hysteria-route.sh`):
```bash
#!/bin/bash
TARGET=<SERVER_1_IP>
IFACE=en1

fix_route() {
    local current_iface gateway
    current_iface=$(/sbin/route get "$TARGET" 2>/dev/null | awk '/interface/{print $2}')
    if [[ "$current_iface" != "$IFACE" ]]; then
        gateway=$(ipconfig getoption "$IFACE" router 2>/dev/null)
        if [[ -z "$gateway" ]]; then
            echo "$(date) no gateway on $IFACE yet, skipping"
            return
        fi
        /sbin/route delete -host "$TARGET" 2>/dev/null
        /sbin/route add -host "$TARGET" "$gateway"
        echo "$(date) fixed route via $gateway ($IFACE)"
    fi
}

fix_route

/sbin/route monitor | while IFS= read -r _line; do
    fix_route
done
```

**Key:** Uses `ipconfig getoption en1 router` to dynamically detect the WiFi gateway rather than hardcoding an IP — this survives subnet changes (e.g. moving between networks).

LaunchDaemon plist at `/tmp/hysteria-route-sequoia.plist` — **not yet installed on Sequoia** (pending).

### Performance (Sequoia)

| Transport | Speed | Notes |
|-----------|-------|-------|
| wstunnel (TCP, old) | ~8.5 KB/s | GFW throttled |
| Hysteria2 (QUIC, WiFi path) | ~750 KB/s | Confirmed working |

---

## Client: Tahoe (device2)

### Network Interfaces

| Interface | Network | Gateway | Notes |
|-----------|---------|---------|-------|
| en0 | 192.168.99.x | 192.168.99.1 | Wired, goes through soft router VPN |
| en1 | 192.168.5.x | 192.168.5.1 | WiFi, direct home router (no VPN) |



### Hysteria2 Client

- Binary: `~/bin/hysteria` (arm64)
- Config: `~/Library/Application Support/hysteria/client.yaml`
- LaunchAgent: `~/Library/LaunchAgents/uk.fireshare.hysteria.plist` (auto-start, auto-restart)
- Log: `/tmp/hysteria-mac.log`

**`client.yaml`:**
```yaml
server: <SERVER_1_IP>:443

auth: <YOUR_AUTH_PASSWORD>

tls:
  sni: <YOUR_HOSTNAME>

udpForwarding:
  - listen: 127.0.0.1:1444
    remote: 127.0.0.1:51820
    timeout: 0s
```

Note: Tahoe uses port **1444** (Sequoia uses 1443). Port **4443** was tried but blocked by the network — port **443** works.

### AmneziaWG Client

- Active profile: **`device2-hysteria`**
- Config file: `~/Documents/Gen8/device2-hysteria.conf` (AirDropped from Sequoia)
- VPN IP: **`10.8.0.3`** (unique key pair — different from Sequoia)

**Important:** Tahoe uses a separate WireGuard key pair (device2). Originally both machines shared device1 keys, causing them to constantly steal each other's WireGuard session on the server (each PersistentKeepalive would evict the other). Creating device2 solved this.

### Routing Fix

Same spurious host route problem as Sequoia. **Fixed and persistent.**

- Script: `/usr/local/bin/fix-hysteria-route.sh` (reactive `route monitor` loop, dynamically detects en1 gateway via `ipconfig getoption en1 router`)
- LaunchDaemon: `/Library/LaunchDaemons/uk.fireshare.hysteria-route.plist` (KeepAlive=true, runs as root)
- Log: `/tmp/hysteria-route.log`

**Critical:** Gateway must NOT be hardcoded. Tahoe's WiFi is on `192.168.5.x` (gateway `192.168.5.1`). When it was hardcoded to `192.168.10.1` (wrong subnet), the dead gateway caused `EADDRNOTAVAIL` errors in Hysteria2's UDP socket, completely breaking the connection.

### Performance (Tahoe)

| Condition | Speed | Notes |
|-----------|-------|-------|
| WiFi + wired both up | ~2.5 MB/s | Confirmed 2026-05-23 |
| WiFi only (wired disconnected) | ~2.0 MB/s | Confirmed 2026-05-23 |

---

## Key Problems Encountered

### 1. GFW TCP Throttling
TCP to <SERVER_1_IP> throttled to ~26 KB/s. Confirmed by 12+ minute SCP of 20 MB file. QUIC (UDP) is unthrottled — Hysteria2 solves this.

### 2. AmneziaWG Port Conflict
AmneziaWG server was on UDP 443, conflicting with Hysteria2. Moved AmneziaWG to UDP 51820 via `awg set wg0 listen-port 51820`.

### 3. Spurious VPN Host Route (macOS Bug)
macOS clones a host route for IPs adjacent to VPN subnet boundaries, even if those IPs are excluded from AllowedIPs. Adding a static host route via the physical gateway overrides this.

### 4. QUIC Timeout Through Soft Router
Hysteria2's QUIC packets were double-tunnelled through the soft router's own VPN, causing NAT/QUIC incompatibility. Fixed by:
- Adding <SERVER_1_IP> and <SERVER_2_IP> to soft router bypass list (direct ISP for these IPs)
- Using WiFi (en1) path that bypasses the soft router entirely

### 5. UDP Port 4443 Blocked
The network blocks UDP 4443. Hysteria2 must use UDP 443.

### 6. WireGuard Key Collision
Both Sequoia and Tahoe originally used the same device1 key pair. The server tracks one endpoint per public key — both machines constantly evicted each other's session, causing near-zero throughput. Fixed by generating a separate key pair (device2) for Tahoe and registering it as a distinct peer on the server.

### 7. Hardcoded Gateway Breaks Hysteria2 on Subnet Change

The route fix script originally hardcoded `192.168.10.1` as the WiFi gateway. When Tahoe's WiFi switched to the `192.168.5.x` subnet (gateway `192.168.5.1`), the static host route pointed to a dead gateway. macOS could not find a valid source address for Hysteria2's UDP socket → `sendmsg: can't assign requested address` (EADDRNOTAVAIL) on every packet → complete connection failure. Fix: use `ipconfig getoption en1 router` to detect the gateway dynamically.

### 8. Route Fix Script: Interface Check Insufficient After Subnet Change

The reactive script checked only `current_iface != IFACE` — but after a subnet change, the static route still showed `interface: en1` (correct interface) with a dead gateway (`192.168.10.1`). The script saw the interface was correct and did nothing, while the gateway pointed to a different subnet. Fix: also compare `current_gateway` vs `ipconfig getoption en1 router` and re-add the route if the gateway is stale.

### 9. macOS SSH MaxAuthTries
SSH from Sequoia to Tahoe kept failing with "Too many authentication failures" due to macOS offering many keys from config. Workaround: use `sshpass` with password auth. Sequoia's public key was added to Tahoe's `~/.ssh/authorized_keys`.

---

## Pending Tasks

1. **iOS/Android clients** — Set up Hysteria2 + AmneziaWG on mobile devices (ShadowRocket or similar supports Hysteria2).

2. **DNS failover** — Configure DNS so clients automatically switch between a1 and tn2 when one server goes down.

---

## File Reference

| File | Location | Purpose |
|------|----------|---------|
| `device1-wstunnel.conf` | `~/Documents/Gen8/` | AmneziaWG profile for Sequoia (Endpoint :1443) |
| `device1-hysteria.conf` | `~/Documents/Gen8/` | AmneziaWG profile for Tahoe v1 (Endpoint :1444, replaced by device2) |
| `device2-hysteria.conf` | `~/Documents/Gen8/` | AmneziaWG profile for Tahoe (Endpoint :1444, unique key) |
| `client.yaml` (Sequoia) | `~/Library/Application Support/hysteria/` | Hysteria2 client, port 1443 |
| `client.yaml` (Tahoe) | `~/Library/Application Support/hysteria/` | Hysteria2 client, port 1444 |
| `uk.fireshare.hysteria.plist` | `~/Library/LaunchAgents/` | Hysteria2 auto-start (both machines) |
| `fix-hysteria-route.sh` | `~/bin/` (Sequoia) | Route fix script — reactive `route monitor` loop |
| `fix-hysteria-route.sh` | `/usr/local/bin/` (Tahoe) | Route fix script — reactive `route monitor` loop |
| `uk.fireshare.hysteria-route.plist` | `/Library/LaunchDaemons/` (Tahoe) | Route fix LaunchDaemon (KeepAlive, runs as root) |
| `server.yaml` | `/etc/hysteria/` on a1 | Hysteria2 server config |
| `server.yaml` | `/etc/hysteria/` on tn2 | Hysteria2 server config |
| `awg0.conf` | `/etc/wireguard/` on a1 | AmneziaWG server config |
| `wg0.conf` | `/etc/amnezia/amneziawg/` on tn2 | AmneziaWG server config |
| `fix-hysteria-route.sh` | `/usr/local/bin/` (Sequoia) | Route fix script — reactive `route monitor` loop |
| `uk.fireshare.hysteria-route.plist` | `/Library/LaunchDaemons/` (Sequoia) | Route fix LaunchDaemon (KeepAlive, runs as root) |
| `your-key.pem` | `~/.ssh/` | SSH key for both servers |
