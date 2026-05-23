# AmneziaWG + Hysteria2 VPN

A VPN stack that bypasses GFW deep-packet inspection and TCP throttling using AmneziaWG (obfuscated WireGuard) tunnelled over Hysteria2 (QUIC/UDP).

## The Problem

GFW applies two layers of interference to overseas VPN traffic:

1. **DPI fingerprinting** — Standard WireGuard handshakes are identified and blocked. Traffic to known VPN IPs is rate-limited or dropped.
2. **TCP throttling** — Even when tunnelled via WebSocket/TLS (wstunnel), GFW throttles TCP connections to overseas endpoints to ~8–26 KB/s, rendering the connection unusable for anything beyond text.

QUIC (UDP) traffic is not subject to the same throttling on these paths. Hysteria2 exploits this by carrying the VPN tunnel over QUIC.

## Design Decisions

### Why AmneziaWG?

Standard WireGuard has a fixed, identifiable handshake structure. AmneziaWG adds:
- **Junk packets** (`Jc`, `Jmin`, `Jmax`) — random-length packets injected before the real handshake to defeat size-based fingerprinting
- **Init packet padding** (`S1`–`S4`) — modifies the handshake packet sizes
- **Magic header values** (`H1`–`H4`) — replaces WireGuard's fixed header bytes, making the protocol unrecognisable to DPI

This allows the WireGuard tunnel to pass through networks that would normally block or fingerprint it.

### Why Hysteria2 over wstunnel?

The previous transport was `wstunnel` (WebSocket over TLS → TCP). Despite looking like HTTPS traffic, GFW throttles TCP connections to flagged ASNs (Alibaba Cloud Singapore) to 8–26 KB/s.

Hysteria2 uses **QUIC** (UDP/443), which is not subject to this throttling on the same paths. Measured results:

| Transport | Throughput | Notes |
|-----------|-----------|-------|
| wstunnel (TCP) | ~8 KB/s | GFW throttled |
| Hysteria2 (QUIC) | 750 KB/s – 3.4 MB/s | Unthrottled |

Hysteria2 also uses a **masquerade** feature: unauthenticated HTTPS requests receive a valid proxy response (Bing homepage), making the server indistinguishable from a real HTTPS server to passive observers.

### Why UDP 443?

UDP 4443 is blocked by many networks. Port 443 is universally allowed (HTTPS/QUIC). Hysteria2 and AmneziaWG's original port 443 conflict is resolved by moving AmneziaWG to UDP 51820 on the server side; the client side only ever sees `127.0.0.1`.

### Why split-tunnel (not full-tunnel)?

AmneziaWG's `AllowedIPs` excludes the Hysteria2 server IPs (`8.222.164.32`, `43.160.238.86`). If these IPs were inside the tunnel, Hysteria2's own QUIC packets would be captured by WireGuard, creating an infinite loop. Split-tunnel ensures Hysteria2 traffic always exits via the physical interface.

### macOS Routing Quirk

macOS clones a host route for the VPN server IP onto the `utun` interface when AmneziaWG activates — even though the IP is excluded from `AllowedIPs`. This cloned route (`UHWIig` flag) intercepts Hysteria2's UDP packets and loops them into the tunnel.

**Fix:** A `route monitor`-based LaunchDaemon (runs as root, reacts instantly to route table changes) maintains a static host route (`UGHS` flag) to the VPN server via the WiFi gateway. This takes precedence over the cloned utun route. The gateway is detected dynamically via `ipconfig getoption en1 router` — hardcoding breaks when the client moves to a different subnet.

### Two-Server Architecture with Failover

Two servers (a1 in Singapore, tn2 in a separate region) provide redundancy:

- **Server-side failover:** Each server monitors the other every 2 minutes. If the peer is unreachable, it removes the peer's A record from Cloudflare DNS (TTL=60s).
- **Client-side failover:** Each client checks whether its current server is reachable every 2 minutes. If not, it switches `client.yaml` to the other server's IP and restarts Hysteria2. This is IP-based (no DNS dependency), which avoids a bootstrap problem: AmneziaWG sets system DNS to the VPN server's address, so DNS resolution fails when the tunnel is down.

---

## Architecture

```
[macOS Client]
  Application traffic
       │
       ▼
  AmneziaWG (utun interface)
  Obfuscated WireGuard — junk packets, modified headers
       │ UDP → 127.0.0.1:1443 (device1) or :1444 (device2)
       ▼
  Hysteria2 client
  QUIC/UDP over port 443
       │
       │  (direct via WiFi en1, bypassing soft router)
       ▼
  ┌─────────────────────┐     ┌─────────────────────┐
  │  a1 (Singapore)     │     │  tn2 (backup)        │
  │  8.222.164.32       │     │  43.160.238.86        │
  │                     │     │                       │
  │  Hysteria2 :443/UDP │     │  Hysteria2 :443/UDP   │
  │       │             │     │       │               │
  │  AmneziaWG :51820   │     │  AmneziaWG :51820     │
  │       │             │     │       │               │
  │  NAT → internet     │     │  NAT → internet       │
  └─────────────────────┘     └─────────────────────┘
           │                           │
           └──────── Cloudflare DNS ───┘
                nebuchadnezzar.fireshare.uk
                   (both A records, TTL=60)
```

DNS contains both server IPs. Each server monitors the other and removes its A record if it goes down. Clients use hardcoded IPs and switch independently via a local watchdog agent.

---

## Deployment

### Prerequisites

- Two Ubuntu servers (systemd)
- A domain managed by Cloudflare
- TLS certificate for your domain (ZeroSSL or Let's Encrypt)
- macOS clients with AmneziaWG app installed

### Server Setup

#### 1. Install AmneziaWG

```bash
# Add AmneziaWG repository and install
add-apt-repository ppa:amnezia/ppa
apt install amneziawg
```

#### 2. Configure AmneziaWG (`/etc/amnezia/amneziawg/wg0.conf` on tn2, `/etc/wireguard/awg0.conf` on a1)

```ini
[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = <SERVER_PRIVATE_KEY>

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
H4 = 99001

PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

[Peer]
# device1 (Sequoia)
PublicKey = <DEVICE1_PUBLIC_KEY>
AllowedIPs = 10.8.0.2/32

[Peer]
# device2 (Tahoe)
PublicKey = <DEVICE2_PUBLIC_KEY>
AllowedIPs = 10.8.0.3/32
```

Enable IP forwarding:
```bash
echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf
sysctl -p
```

Start AmneziaWG:
```bash
# a1
systemctl enable --now wg-quick@awg0
# tn2
systemctl enable --now awg-quick@wg0
```

#### 3. Install TLS Certificate

```bash
mkdir -p /etc/ssl/<your-domain>
# Copy fullchain.pem and key.pem from your CA (ZeroSSL/Let's Encrypt)
```

#### 4. Install Hysteria2

```bash
curl -fsSL https://github.com/apernet/hysteria/releases/latest/download/hysteria-linux-amd64 \
  -o /usr/local/bin/hysteria
chmod +x /usr/local/bin/hysteria
```

Create `/etc/hysteria/server.yaml`:
```yaml
listen: :443

tls:
  cert: /etc/ssl/<your-domain>/fullchain.pem
  key: /etc/ssl/<your-domain>/key.pem

auth:
  type: password
  password: <YOUR_AUTH_PASSWORD>

masquerade:
  type: proxy
  proxy:
    url: https://www.bing.com/
    rewriteHost: true
```

Create `/etc/systemd/system/hysteria.service`:
```ini
[Unit]
Description=Hysteria2 Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/hysteria server --config /etc/hysteria/server.yaml
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now hysteria
```

#### 5. Set Up Server-Side Failover

See `server/failover.sh`. Deploy to both servers, set `MY_IP` and `PEER_IP`, then add to crontab:

```bash
chmod +x /usr/local/bin/hysteria-failover.sh
(crontab -l; echo '*/2 * * * * /usr/local/bin/hysteria-failover.sh') | crontab -
```

Requires a Cloudflare API token with `Zone:DNS:Edit` permission and your Zone ID.

### Client Setup (macOS)

#### 1. Generate Key Pairs

Each device needs a unique key pair. In the AmneziaWG app: Settings → Add tunnel → Create from scratch, or via CLI:

```bash
awg genkey | tee private.key | awg pubkey > public.key
```

Register each device's public key on the server with a unique VPN IP (`10.8.0.2`, `10.8.0.3`, etc.).

#### 2. AmneziaWG Profile

Create an AmneziaWG tunnel profile pointing to `127.0.0.1:<local-hysteria2-port>`:

```ini
[Interface]
PrivateKey = <DEVICE_PRIVATE_KEY>
Address = 10.8.0.<N>/32
DNS = 10.8.0.1
MTU = 1280
Jc = 4
Jmin = 40
Jmax = 70
S1 = 30 / S2 = 40 / S3 = 30 / S4 = 40
H1 = 11223 / H2 = 44556 / H3 = 77889 / H4 = 99001

[Peer]
PublicKey = <SERVER_PUBLIC_KEY>
Endpoint = 127.0.0.1:1443
AllowedIPs = 0.0.0.0/1, 128.0.0.0/2, 192.0.0.0/9, 192.128.0.0/11, ...
             # All IPs EXCEPT the Hysteria2 server IPs (8.222.164.32, 43.160.238.86)
PersistentKeepalive = 25
```

Import this profile into the AmneziaWG macOS app.

#### 3. Install Hysteria2 Client

Download the arm64 binary:
```bash
curl -fsSL https://github.com/apernet/hysteria/releases/latest/download/hysteria-darwin-arm64 \
  -o ~/bin/hysteria
chmod +x ~/bin/hysteria
```

Create `~/Library/Application Support/hysteria/client.yaml` (see `client/hysteria-client-device1.yaml`).

#### 4. Install LaunchAgents and LaunchDaemons

**Hysteria2 auto-start** (LaunchAgent, runs as user):
```bash
cp client/uk.fireshare.hysteria.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria.plist
```

**Route fix** (LaunchDaemon, runs as root — required for `route` commands):
```bash
sudo cp client/fix-hysteria-route.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/fix-hysteria-route.sh
sudo cp client/uk.fireshare.hysteria-route.plist /Library/LaunchDaemons/
sudo launchctl bootstrap system /Library/LaunchDaemons/uk.fireshare.hysteria-route.plist
```

**Client failover** (LaunchAgent, runs as user):
```bash
cp client/hysteria-failover-client.sh ~/bin/
chmod +x ~/bin/hysteria-failover-client.sh
cp client/uk.fireshare.hysteria-failover.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria-failover.plist
```

#### 5. Add Server IPs to AmneziaWG AllowedIPs Exclusion

Both Hysteria2 server IPs must be excluded from the WireGuard tunnel's `AllowedIPs`. If they are included, Hysteria2's own packets get captured by WireGuard → infinite loop.

---

## Maintenance

### Adding a New Device

1. Generate a key pair on the new device
2. Add the public key to `/etc/amnezia/amneziawg/wg0.conf` (or `/etc/wireguard/awg0.conf`) on both servers with a new VPN IP
3. Run `awg syncconf wg0 <(awg-quick strip /path/to/wg0.conf)` to apply without restart
4. Create AmneziaWG profile and `client.yaml` from the templates in `client/`
5. Install LaunchAgents and LaunchDaemon as above

### Rotating Credentials

- **Hysteria2 auth password:** Update `server.yaml` on both servers, update `client.yaml` on all clients, restart services.
- **WireGuard keys:** Generate new keypair, update server peer entry, update client profile, restart AmneziaWG.
- **TLS certificate renewal:** ZeroSSL/ACME renewal updates `/etc/ssl/<domain>/`. Restart `hysteria` service after renewal.

### Checking Status

```bash
# Server
systemctl status hysteria
systemctl status awg-quick@wg0    # tn2
systemctl status wg-quick@awg0   # a1
awg show

# Client
launchctl list | grep fireshare
tail -f /tmp/hysteria-mac.log
tail -f /tmp/hysteria-route.log
tail -f /tmp/hysteria-failover-client.log

# Network
route get 8.222.164.32           # Should show via en1 (WiFi), not utun
curl https://ipinfo.io/ip        # Should return VPN server IP when tunnel is up
```

### Failover Testing

To simulate a server failure:
```bash
# On the server to be "failed":
systemctl stop hysteria

# Watch the monitoring logs on the peer server (within 2 minutes):
tail -f /var/log/hysteria-failover.log

# Watch the client switch (within 2 minutes):
tail -f /tmp/hysteria-failover-client.log
```

### Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Hysteria2 `sendmsg: can't assign requested address` | Static route pointing to dead gateway | Check `netstat -rn \| grep <server-ip>`, verify gateway matches `ipconfig getoption en1 router` |
| Hysteria2 connects but near-zero throughput | QUIC packets looping through VPN | Verify server IPs are excluded from AmneziaWG AllowedIPs; check route fix daemon is running |
| Both devices same key pair → trickling speed | WireGuard session collision | Generate separate key pair for each device |
| `lookup hostname: no such host` on startup | AmneziaWG sets DNS to VPN server; circular dependency | Use server IP directly in `client.yaml`, not hostname |
| Route fix daemon bootstrap fails (I/O error 5) | Script path in user home directory inaccessible at boot | Move script to `/usr/local/bin/`, not `~/bin/` |

---

## Repository Structure

```
.
├── README.md
├── docs/
│   └── setup.md              # Full deployment diary and problem log
├── server/
│   ├── hysteria-server.yaml  # Hysteria2 server config template
│   └── failover.sh           # Server-side Cloudflare DNS failover script
├── client/
│   ├── hysteria-client-device1.yaml      # Hysteria2 client config (device1/port 1443)
│   ├── hysteria-client-device2.yaml      # Hysteria2 client config (device2/port 1444)
│   ├── fix-hysteria-route.sh             # macOS route fix (reactive, dynamic gateway)
│   ├── hysteria-failover-client.sh       # Client-side server failover script
│   ├── uk.fireshare.hysteria.plist       # Hysteria2 LaunchAgent
│   ├── uk.fireshare.hysteria-route.plist # Route fix LaunchDaemon (runs as root)
│   └── uk.fireshare.hysteria-failover.plist  # Failover LaunchAgent
└── amneziawg/
    └── device-template.conf  # AmneziaWG client profile template
```

---

## Security Notes

- Never commit private keys, auth passwords, or API tokens to this repository. Use the `<PLACEHOLDER>` values in templates.
- The Hysteria2 masquerade (Bing proxy) provides passive traffic analysis resistance but is not a substitute for proper OpSec.
- The server-side failover script stores the Cloudflare API token in a file. Restrict permissions: `chmod 600 /usr/local/bin/hysteria-failover.sh`.
- Each device must have its own unique WireGuard key pair. Shared keys cause session collision and destroy throughput.
