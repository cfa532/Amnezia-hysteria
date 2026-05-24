# macOS Client Setup

This guide explains how to deploy the VPN client on macOS for the full-stack architecture: **AmneziaWG tunnelled inside Hysteria2 (QUIC/UDP)**, with automatic server failover and split routing (Chinese IPs bypass the VPN).

```
AmneziaWG app ──UDP──▶ 127.0.0.1:1443 (Hysteria2 UDP forwarder)
                              │
                    Hysteria2 client (QUIC over UDP :80)
                              │
                    VPN server — nebuchadnezzar.fireshare.uk
                              │
                         awg0 (AmneziaWG)
                              │
                           internet
```

> **Tahoe is not a regular client.** Tahoe (mac2) has a special dual-NIC setup used for debugging. Its wired interface (en0) is connected to a soft router that has its own built-in VPN; its WiFi interface (en1) is connected to a home router with no VPN and is the interface used for testing. See [Part 3](#part-3--tahoe-special-setup-mac2-only) for Tahoe-specific instructions.

---

## Prerequisites

- macOS 13 Ventura or later
- Your provisioned config files from the admin:
  - `macN.conf` — AmneziaWG tunnel config (unique to your device)
  - `macN-servers.conf` — Hysteria2 server list for your region

---

## Part 1 — AmneziaWG

### 1.1 Install AmneziaWG

Download from the Mac App Store: search **AmneziaWG**.

### 1.2 Import your config

1. Open AmneziaWG → **+** → **Import tunnel(s) from file**
2. Select your `macN.conf`
3. Click **Allow** when macOS prompts to add a VPN configuration

> **Each `.conf` file is device-specific.** Two devices sharing the same file will knock each other offline every ~25 seconds (WireGuard's session rekey).

The tunnel config has `Endpoint = 127.0.0.1:1443`. This is intentional — it connects to the local Hysteria2 forwarder, not directly to the server. Do not change it.

---

## Part 2 — Hysteria2 (regular clients)

Hysteria2 runs as a background daemon. It connects to the VPN server over QUIC (UDP port 80) and exposes a local UDP port that AmneziaWG connects to.

### 2.1 Install the Hysteria2 binary

```bash
mkdir -p ~/bin
ARCH=$(uname -m | sed 's/x86_64/amd64/;s/arm64/arm64/')
curl -L "https://github.com/apernet/hysteria/releases/latest/download/hysteria-darwin-${ARCH}" \
     -o ~/bin/hysteria
chmod +x ~/bin/hysteria
~/bin/hysteria version
```

### 2.2 Create the config directory

```bash
mkdir -p ~/Library/Application\ Support/hysteria
```

### 2.3 Write servers.conf

Use the `macN-servers.conf` file provided by the admin. It lists your preferred server first:

```bash
cp macN-servers.conf ~/Library/Application\ Support/hysteria/servers.conf
```

Example format:
```
# ip           region      port
8.222.164.32   singapore   80
43.160.238.86  singapore   80
```

### 2.4 Write client.yaml

This derives the initial server address from servers.conf so nothing is hardcoded:

```bash
FIRST_SERVER=$(grep -Ev '^\s*(#|$)' \
    ~/Library/Application\ Support/hysteria/servers.conf \
    | awk 'NR==1{print $1":"$3}')

cat > ~/Library/Application\ Support/hysteria/client.yaml << EOF
server: ${FIRST_SERVER}

auth: morphous-hy2-2026

tls:
  sni: nebuchadnezzar.fireshare.uk
  insecure: false

transport:
  udp:
    hopInterval: 0s

udpForwarding:
  - listen: 127.0.0.1:1443
    remote: 127.0.0.1:51820
    timeout: 0s
EOF
```

The failover script will update `server:` automatically if the active server becomes unreachable.

### 2.5 Fix the routing loop

When AmneziaWG is active, all traffic is tunnelled through the VPN — including Hysteria2's own QUIC connection to the server. Without intervention, Hysteria2 would try to send its UDP packets through the tunnel, which loops back through Hysteria2, and the connection collapses.

**For regular clients (single physical NIC):** add permanent host routes for the server IPs via your default gateway. The route-fix daemon does this automatically.

```bash
sudo cp ~/Documents/GitHub/Amnezia-hysteria/client/fix-hysteria-route.sh \
     /usr/local/bin/fix-hysteria-route.sh
sudo chmod +x /usr/local/bin/fix-hysteria-route.sh

sudo cp ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.hysteria-route.plist \
     /Library/LaunchDaemons/

sudo launchctl bootstrap system \
     /Library/LaunchDaemons/uk.fireshare.hysteria-route.plist
```

Verify routes are in place:
```bash
route get 8.222.164.32   # interface should be en0 or en1, NOT utunX
route get 43.160.238.86
```

> **Tahoe cannot use this approach.** Its host routes would be absorbed by the soft router's VPN on en0, not bypassed. Tahoe uses the UDP proxy instead — see [Part 3](#part-3--tahoe-special-setup-mac2-only).

### 2.6 Install the Hysteria2 LaunchAgent

```bash
sed "s|<USERNAME>|$(whoami)|g" \
    ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.hysteria.plist \
    > ~/Library/LaunchAgents/uk.fireshare.hysteria.plist

launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria.plist
```

Verify it started:
```bash
launchctl list | grep hysteria
tail -20 /tmp/hysteria-mac.log
```

### 2.7 Install the failover monitor

The failover script runs every 2 minutes, pings the active server, and round-robins to the next one if unreachable.

```bash
cp ~/Documents/GitHub/Amnezia-hysteria/client/hysteria-failover-client.sh \
   ~/bin/hysteria-failover-client.sh
chmod +x ~/bin/hysteria-failover-client.sh

cp ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.hysteria-failover.plist \
   ~/Library/LaunchAgents/

launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria-failover.plist
```

---

## Part 3 — Tahoe special setup (mac2 only)

> This section applies only to the Tahoe machine. Do not follow these steps on a regular client.

### Why Tahoe is different

Tahoe has two physical network interfaces:

| Interface | Network | VPN |
|-----------|---------|-----|
| en0 (wired) | Soft router | Built-in VPN on the router |
| en1 (WiFi) | Home router | No VPN — used for testing |

When we test the VPN on Tahoe, the tunnel traffic must exit via **en1**. The soft router on en0 would intercept and re-wrap UDP traffic in its own VPN, making the test meaningless and breaking the connection.

Host routes cannot fix this: macOS would still route packets out via en0 (the soft router's VPN path) because en0 is the default route. We need the outgoing socket itself to be bound to the en1 interface.

### Solution: UDP proxy bound to en1

`hysteria-udp-proxy.py` sits between Hysteria2 and the server. Hysteria2 sends its QUIC packets to the proxy on `127.0.0.1:9443`; the proxy creates outgoing UDP sockets bound to en1's IP address, ensuring server traffic exits via WiFi regardless of the routing table.

```
Hysteria2 client ──UDP──▶ 127.0.0.1:9443 (hysteria-udp-proxy.py)
                                  │  (socket bound to en1)
                           UDP :80 via WiFi (en1)
                                  │
                           VPN server
```

### 3.1 Install the proxy

```bash
mkdir -p ~/bin
cp ~/Documents/GitHub/Amnezia-hysteria/client/hysteria-udp-proxy.py ~/bin/
chmod +x ~/bin/hysteria-udp-proxy.py
```

Install the LaunchAgent:
```bash
sed "s|REPLACE_USER|$(whoami)|g" \
    ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.hysteria-proxy.plist \
    > ~/Library/LaunchAgents/uk.fireshare.hysteria-proxy.plist

launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria-proxy.plist
```

Verify:
```bash
launchctl list | grep hysteria-proxy
tail -10 /tmp/hysteria-proxy.log
# Should show: proxy up on 127.0.0.1:9443
```

### 3.2 client.yaml for Tahoe

Tahoe's Hysteria2 client points to the proxy, not directly to the server:

```bash
cat > ~/Library/Application\ Support/hysteria/client.yaml << 'EOF'
server: 127.0.0.1:9443

auth: morphous-hy2-2026

tls:
  sni: nebuchadnezzar.fireshare.uk
  insecure: false

transport:
  udp:
    hopInterval: 0s

udpForwarding:
  - listen: 127.0.0.1:1443
    remote: 127.0.0.1:51820
    timeout: 0s
EOF
```

### 3.3 servers.conf for Tahoe

```
# ip            region      port
43.160.238.86   singapore   80
8.222.164.32    singapore   80
```

(tn2 listed first — Tahoe's preferred server is tn2.)

### 3.4 Load order

The proxy must start before Hysteria2. Load in this order:

```bash
launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria-proxy.plist
launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria.plist
launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria-failover.plist
```

### 3.5 Verify Tahoe is working

```bash
# Proxy is up
tail -5 /tmp/hysteria-proxy.log

# Hysteria2 connected (look for "connected to server")
tail -20 /tmp/hysteria-mac.log

# AWG shows a handshake
# Toggle AmneziaWG tunnel off then on, wait ~5s, check the handshake timestamp in the app

# Traffic exits via VPN server IP (not ISP IP)
curl -s https://api.ipify.org
```

---

## Split routing

Your provisioned `.conf` has `AllowedIPs` set to all non-Chinese IP ranges. Chinese websites and services route direct (via your ISP); everything else goes through the VPN.

If you were provisioned with `routing=full` instead, `AllowedIPs = 0.0.0.0/0, ::/0` and all traffic goes through the VPN including Chinese sites. Contact the admin if you need to switch.

---

## Verification

```bash
# Hysteria2 daemon running
launchctl list uk.fireshare.hysteria

# Hysteria2 log (look for "connected to server")
tail -30 /tmp/hysteria-mac.log

# Failover monitor log
tail -20 /tmp/hysteria-failover-client.log

# Route protection (regular clients only)
route get 8.222.164.32    # should show en0/en1, not utunX

# Exit IP
curl -s https://api.ipify.org   # should return VPN server IP
```

---

## Troubleshooting

### Hysteria2 keeps restarting

```bash
tail -50 /tmp/hysteria-mac.log
```

Common causes:
- **TLS error**: SNI mismatch — `sni:` in client.yaml must be `nebuchadnezzar.fireshare.uk`
- **Connection refused**: server is down, failover will pick another within 2 minutes
- **Auth failed**: wrong `auth:` value — must be `morphous-hy2-2026`

### AmneziaWG shows no handshake

1. Confirm Hysteria2 is running: `launchctl list uk.fireshare.hysteria`
2. Confirm the forwarder port is open: `netstat -an | grep 1443`
3. Confirm the tunnel endpoint is `127.0.0.1:1443` (not the server IP)

### Routing loop (no internet in tunnel)

For regular clients — check the route-fix daemon:
```bash
sudo launchctl list uk.fireshare.hysteria-route
route get 8.222.164.32   # must NOT show utunX
sudo /usr/local/bin/fix-hysteria-route.sh   # manual fix
```

### Tahoe: proxy not forwarding

```bash
tail -20 /tmp/hysteria-proxy.log
ipconfig getifaddr en1   # must return an IP; if blank, WiFi is not connected
```

If en1 has no IP, the proxy waits up to 60 seconds for it to appear. Connect to WiFi first, then restart the proxy:
```bash
launchctl kickstart -k gui/$(id -u)/uk.fireshare.hysteria-proxy
```

### Failover not switching

The failover script pings servers directly. On regular clients, this requires the route-fix daemon to be running (otherwise pings go into the tunnel and always succeed, masking a down server on the outer path). On Tahoe, pings go via en1 automatically if the proxy is routing there.
