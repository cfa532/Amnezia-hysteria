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

Two transport modes are supported depending on the client type:

### Mode A — Hysteria2 Transport (macOS, high-throughput)

```
[Client: Mac]
  App traffic
    ↓
  AmneziaWG (utun, obfuscated WireGuard)
    ↓ UDP to 127.0.0.1:1443 (Sequoia) or 127.0.0.1:1444 (Tahoe)
  Hysteria2 client (QUIC/UDP)
    ↓ QUIC over UDP port 443 → ISP → internet
  Hysteria2 server (<YOUR_DOMAIN>:443)
    ↓ UDP forwarded to 127.0.0.1:53
  AmneziaWG server (awg0, port 53)
    ↓
  Internet (NAT via eth0)
```

### Mode B — Direct AmneziaWG (iOS, Android, additional macOS)

```
[Client: iOS / Android / Mac]
  App traffic
    ↓
  AmneziaWG (obfuscated WireGuard)
    ↓ UDP directly to <YOUR_DOMAIN>:443
  AmneziaWG server (awg0, port 443)
    ↓
  Internet (NAT via eth0)
```

Mode B is used for mobile devices because Hysteria2 apps are unavailable in the China App Store. ISP/NAT only allows UDP 443, so awg0 must run on port 443. Both servers share the same awg0 private key so DNS round-robin is transparent to clients.

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

**Services (per server):**

| Service | Port | Config | Notes |
|---------|------|--------|-------|
| `awg-quick@awg0` | UDP/53 | `/etc/amnezia/amneziawg/awg0.conf` | iOS/Android direct + macOS via Hysteria2 loopback |
| `hysteria` | UDP/443 | `/etc/hysteria/server.yaml` | Hysteria2 QUIC transport for macOS clients |

> **Note:** AWG uses port 53 (DNS) because UDP 80 is blocked by many home routers. Port 53 is open on virtually all networks. Before AWG can bind to port 53, systemd-resolve's stub listener must be disabled — see server setup step below.

#### AmneziaWG Server (awg0)

- Config: `/etc/amnezia/amneziawg/awg0.conf`  (see `server/awg0-server.conf` template)
- Listen port: **UDP 53** — chosen for universal firewall traversal (DNS port)
- Subnet: `10.8.0.0/24`, Server IP: `10.8.0.1`

**Prerequisite — free port 53 from systemd-resolve:**
```bash
echo 'DNSStubListener=no' >> /etc/systemd/resolved.conf
systemctl restart systemd-resolved
# Verify: ss -unlp | grep ':53' should show nothing on 0.0.0.0
```
- Same private key on all servers — identical pubkey presented to every client

**Critical iptables setup** — UFW runs before appended rules; FORWARD rules must be inserted at position 1:
```bash
# In awg0.conf PostUp (NOT -A, which appends after UFW and is silently dropped):
iptables -I FORWARD 1 -i awg0 -j ACCEPT
iptables -I FORWARD 1 -o awg0 -j ACCEPT
```

**Watch for routing conflicts:** If another WireGuard interface (e.g. `wg0`) is up with the same subnet, the kernel routes return packets through the wrong interface and clients get no responses. Always disable/stop conflicting interfaces:
```bash
systemctl disable awg-quick@wg0
ip link set wg0 down
```

#### Hysteria2 Server (port 4443) — Transport Mode

- Config: `/etc/hysteria/server-4443.yaml`
- Service: `systemctl status hysteria-4443`
- Template: `server/hysteria-server.yaml`

The original hysteria.service (port 443) is removed — awg0 owns port 443.

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
    remote: 127.0.0.1:53
    timeout: 0s
```

Hysteria2 listens on `127.0.0.1:1443` and forwards all UDP to the server's AmneziaWG port (53 on 127.0.0.1 relative to the server).

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
    remote: 127.0.0.1:53
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
AmneziaWG server was on UDP 443, conflicting with Hysteria2. Moved AmneziaWG to UDP 53 via `awg set wg0 listen-port 53`.

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

## Direct AmneziaWG Clients (Mode B)

For iOS, Android, and additional macOS devices. No Hysteria2 required — AmneziaWG connects directly to `<YOUR_DOMAIN>:443`.

### iOS / Android

Provision iOS and Android devices through the controller so the client key is
registered on every VPN server. Run this on the controller server, or SSH to
TN2 first:

```bash
sshpass -p '<TN2_PASSWORD>' ssh -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null root@43.160.238.86
```

Then provision the device:

```bash
cd /opt/vpn-controller
PROVISION_TOKEN='<BEARER_TOKEN>' ./reprovision.sh ios1 ios split /tmp
PROVISION_TOKEN='<BEARER_TOKEN>' ./reprovision.sh android1 android split /tmp
```

The command attempted from the admin machine during testing was the same
password SSH form above, with a remote health check appended:

```bash
sshpass -p '<TN2_PASSWORD>' ssh -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null root@43.160.238.86 \
  'hostname && systemctl is-active vpn-provision || true'
```

If manual provisioning is required, import `amneziawg/ios-direct-template.conf`
into the AmneziaWG app and generate a unique keypair per device:

```bash
awg genkey | tee device.priv | awg pubkey > device.pub
```
Add the peer to `/etc/amnezia/amneziawg/awg0.conf` on **all servers** (same peer list on every server):
```ini
[Peer]
# <device-name>
PublicKey = <DEVICE_PUBLIC_KEY>
AllowedIPs = 10.8.0.<N>/32
```
Then reload: `awg syncconf awg0 <(awg-quick strip awg0)`

#### Mobile split routing

iOS showed unreliable handshakes with the honest full split list
(`~198 KB`, `11975` routes). A reduced split list under `128 KB` connects
reliably. Taobao product detail pages were still blocked until Alibaba/Taobao
cloud and CDN prefixes were excluded from `AllowedIPs`, forcing that traffic
direct. The current mobile recommendation is:

- iOS/Android: use the reduced Taobao-direct split list.
- macOS: keep the honest full split list; macOS does not have the same config
  size limit.
- IPv6: keep disabled in client `AllowedIPs` for the current environment.

### macOS (additional machines)

Use `amneziawg/mac-direct-template.conf`. Key difference from mobile: `AllowedIPs` uses split-route to avoid the macOS 26.5 sendmsg bug — `0.0.0.0/0` causes the VPN interface to become the default route before the handshake, breaking the handshake itself on some macOS versions.

### IP Assignment

| Device | VPN IP |
|--------|--------|
| mac1 | 10.8.0.2 |
| mac2 | 10.8.0.3 |
| mac3 | 10.8.0.4 |
| ios1 | 10.8.0.5 |
| ios2 | 10.8.0.6 |
| android1 | 10.8.0.7 |
| android2 | 10.8.0.8 |
| Next device | 10.8.0.9, 10.8.0.10, ... |

---

## Pending Tasks

1. **Reboot test persistence** — Verify awg0 starts cleanly after reboot on both servers (no wg0 conflict, no Hysteria2 on 443).
2. **iOS/Android setup** — Import ios1/ios2 configs via QR code from AmneziaWG app.
3. **Commercial expansion** — Current shared-private-key approach is for personal use only. At scale, replace with per-server keypairs + health-check LB (Cloudflare/AWS NLB) + management plane (Headscale or custom).

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
