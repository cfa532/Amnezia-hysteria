# macOS Client Setup

This guide sets up a complete VPN client on macOS with two transport modes:

| Mode | Transport | When to use |
|------|-----------|-------------|
| **Hysteria2** | QUIC (UDP 443) → AmneziaWG | Primary — higher throughput, QUIC obfuscation |
| **Direct AmneziaWG** | UDP 443 | Fallback — simpler, lower overhead |

```
[Hysteria2 mode]
AmneziaWG app ──UDP──▶ 127.0.0.1:1443
                              │
                    Hysteria2 client (QUIC)
                              │ UDP 443
                    VPN server (Hysteria2)
                              │ loopback
                         awg0 :443
                              │
                         internet

[Direct mode]
AmneziaWG app ──UDP 443──▶ VPN server awg0 ──▶ internet
```

---

## Prerequisites

- macOS 13 Ventura or later
- Your provisioned config file (`macN.conf`) from the admin

---

## Part 1 — AmneziaWG

### 1.1 Install AmneziaWG

Download from the Mac App Store: search **AmneziaWG**.

### 1.2 Import your config

1. Open AmneziaWG → **+** → **Import tunnel(s) from file**
2. Select your `macN.conf`
3. Click **Allow** when macOS prompts to add VPN configuration

> **Each `.conf` file is unique to one device.** Two devices sharing the same file will knock each other offline every 25 seconds.

### 1.3 Test direct mode

Toggle the tunnel on. Verify:

```bash
# Confirm tunnel is up and has a VPN address
ifconfig | grep 'inet 10\.8\.'

# Confirm traffic exits through the VPN server
curl -s https://api.ipify.org    # should return the server's IP, not your ISP's
```

---

## Part 2 — Hysteria2

Hysteria2 wraps AmneziaWG in QUIC for higher throughput and an extra obfuscation layer. The Hysteria2 client runs as a background daemon and exposes a local UDP port that AmneziaWG connects to.

### 2.1 Install the Hysteria2 binary

```bash
mkdir -p ~/bin
# Download latest release for macOS arm64 (Apple Silicon) or amd64 (Intel)
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

### 2.3 Write client.yaml

```bash
cat > ~/Library/Application\ Support/hysteria/client.yaml << 'EOF'
server: nebuchadnezzar.fireshare.uk:443

auth: morphous-hy2-2026

tls:
  sni: nebuchadnezzar.fireshare.uk
  insecure: false

transport:
  udp:
    hopInterval: 0s

# Local UDP forwarder: AmneziaWG sends to 127.0.0.1:1443,
# Hysteria2 tunnels those packets to awg0 on the server's localhost.
udpForwarding:
  - listen: 127.0.0.1:1443
    remote: 127.0.0.1:443
    timeout: 0s
EOF
```

### 2.4 Write servers.conf

```bash
cat > ~/Library/Application\ Support/hysteria/servers.conf << 'EOF'
# Hysteria2 server list — one per line
# Format: <ip>   <region>   <port>
8.222.164.32    singapore   443
43.160.238.86   backup      443
EOF
```

### 2.5 Install the Hysteria2 LaunchAgent (auto-start)

```bash
# Copy the plist from the repo (or write it manually)
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

### 2.6 Install the failover monitor

The failover script runs every 2 minutes, pings the current Hysteria2 server,
and switches to the next one if unreachable.

```bash
sudo cp ~/Documents/GitHub/Amnezia-hysteria/client/hysteria-failover-client.sh \
     /usr/local/bin/hysteria-failover-client.sh
sudo chmod +x /usr/local/bin/hysteria-failover-client.sh

cp ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.hysteria-failover.plist \
   ~/Library/LaunchAgents/

launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria-failover.plist
```

### 2.7 Install the route-fix daemon

When AmneziaWG is active, all traffic is tunneled — including Hysteria2's own connection
to the VPN server, which would cause a routing loop. The route-fix daemon adds permanent
host routes for the server IPs via your physical interface, bypassing the tunnel.

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
route get 8.222.164.32   # should show interface: en0 (or en1), not utunX
route get 43.160.238.86
```

---

## Part 3 — Switch AmneziaWG to Hysteria2 mode

In Hysteria2 mode, AmneziaWG connects to the local Hysteria2 forwarder instead of
directly to the server. You need a separate tunnel config for this.

### 3.1 Create a Hysteria2-mode config

```bash
# Copy your existing config
cp ~/Documents/Gen8/mac2.conf ~/Documents/Gen8/mac2-hy2.conf

# Change the endpoint from the server address to the local Hysteria2 port
sed -i '' 's/^Endpoint = .*/Endpoint = 127.0.0.1:1443/' ~/Documents/Gen8/mac2-hy2.conf
```

Verify the change:
```bash
grep Endpoint ~/Documents/Gen8/mac2-hy2.conf
# Should show: Endpoint = 127.0.0.1:1443
```

### 3.2 Import the Hysteria2-mode config into AmneziaWG

1. AmneziaWG → **+** → **Import tunnel(s) from file**
2. Select `mac2-hy2.conf`
3. Name it something like `mac2-hy2` to distinguish from the direct config

### 3.3 Connect via Hysteria2

1. Make sure the Hysteria2 LaunchAgent is running (`launchctl list | grep hysteria`)
2. Enable the `mac2-hy2` tunnel in AmneziaWG (disable `mac2` first if it was on)

Verify:
```bash
# AmneziaWG should show a handshake (connects to 127.0.0.1:1443 → Hysteria2 → server)
# Your exit IP should still be the VPN server's IP
curl -s https://api.ipify.org
```

---

## Switching Between Modes

| Mode | What to do |
|------|-----------|
| **Hysteria2** | Enable `mac2-hy2` in AmneziaWG, disable `mac2` |
| **Direct** | Enable `mac2` in AmneziaWG, disable `mac2-hy2` |

Hysteria2 daemon runs in the background either way — it's harmless when AmneziaWG is in direct mode.

---

## Verification

```bash
# Check Hysteria2 is running
launchctl list uk.fireshare.hysteria

# Check Hysteria2 log
tail -30 /tmp/hysteria-mac.log

# Check failover log
tail -20 /tmp/hysteria-failover-client.log

# Check route protection
route get 8.222.164.32

# Check your exit IP
curl -s https://api.ipify.org
```

---

## Troubleshooting

### Hysteria2 keeps restarting

Check the log for the error:
```bash
tail -50 /tmp/hysteria-mac.log
```

Common causes:
- **TLS error**: server hostname doesn't match cert — check `sni:` in `client.yaml`
- **Connection refused**: Hysteria2 server not running on the VPN server — contact admin
- **Auth failed**: wrong password in `auth:` field

### AmneziaWG shows no handshake in Hysteria2 mode

1. Confirm Hysteria2 is running: `launchctl list uk.fireshare.hysteria`
2. Confirm the local forwarder is listening: `netstat -an | grep 1443`
3. Check that the tunnel config endpoint is `127.0.0.1:1443` (not the server IP)

### Routing loop / no internet in Hysteria2 mode

The route-fix daemon may not be running:
```bash
sudo launchctl list uk.fireshare.hysteria-route
tail -20 /tmp/hysteria-route.log

# Manual fix if needed
route get 8.222.164.32   # should NOT show utunX as interface
sudo /usr/local/bin/fix-hysteria-route.sh
```

### Conflict with soft router

If your Mac is behind a router that runs its own VPN, it may intercept UDP 443 before
it reaches the server. Add the VPN server IPs (8.222.164.32, 43.160.238.86) to the
router's bypass list (direct ISP routing).

### macOS 26.x — tunnel up but curl hangs

The direct-mode config already uses split-route `AllowedIPs` to work around the macOS 26.5
sendmsg bug. If you see this with a config using `0.0.0.0/0`, replace it with:
```ini
AllowedIPs = 0.0.0.0/1, 128.0.0.0/1, ::/1, 8000::/1
```

### Failover not switching servers

Check that the ping test can reach the servers without going through the tunnel:
```bash
ping -c 3 8.222.164.32   # should succeed via physical NIC
```
If it hangs, the route-fix daemon is not running (see above).

---

## Notes

- **DNS**: both modes use `8.8.8.8, 1.1.1.1` via the tunnel. Edit `DNS =` before importing if you prefer different resolvers.
- **Full-tunnel routing**: all traffic (including Chinese sites) exits through the VPN. Split-tunnel by destination country is a future feature.
- **Per-device configs**: never share `.conf` files between devices. Each device needs its own provisioned config.
- **Server failover**: the health controller (running on tn2) removes a failed server from DNS within ~90 seconds. If your tunnel drops, toggle it off and on — DNS will resolve to the healthy server. If you provisioned against the failed server, contact the admin to reprovision your device.
