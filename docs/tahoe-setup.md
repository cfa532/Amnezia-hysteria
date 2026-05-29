# Tahoe Setup

> **Setup script available.** Tahoe (mac2) and Sequoia (mac1) share the same dual-NIC topology. Use the shared setup script instead of following these manual steps:
> ```bash
> cd ~/Documents/GitHub/Amnezia-hysteria/client
> ./setup-dual-nic.sh mac2
> ```
> See [dual-nic-setup.md](dual-nic-setup.md) for full details.

Tahoe (mac2) is a **debug machine**, not a regular VPN client. Its purpose is to test the VPN stack from a clean internet path. This document covers its specific setup.

---

## Network topology

Tahoe has two physical network interfaces:

| Interface | Connected to | VPN |
|-----------|-------------|-----|
| en0 (wired, 192.168.5.x) | Soft router | Built-in VPN on the router |
| en1 (WiFi) | Home router | No VPN — used for VPN testing |

All VPN testing uses **en1**. The wired interface en0 leads to a soft router that runs its own VPN; any traffic sent through en0 gets re-wrapped in that router's VPN, which both pollutes the test results and breaks the Hysteria2 connection.

---

## Why the regular setup does not work on Tahoe

Regular clients solve the routing loop problem with host routes: permanent routes for the server IPs that point at the physical gateway instead of the tunnel interface. On a single-NIC machine this works because there is only one physical gateway.

On Tahoe, the default route goes through en0 → soft router → that router's VPN. A host route added to the routing table still exits through en0. To get clean traffic out, the outgoing socket itself must be bound to en1's IP address — the routing table cannot enforce this.

---

## Architecture

Tahoe runs an extra component between Hysteria2 and the server: `hysteria-udp-proxy.py`.

```
AmneziaWG app ──UDP──▶ 127.0.0.1:1443 (Hysteria2 UDP forwarder)
                              │
                    Hysteria2 client (QUIC)
                              │
                    127.0.0.1:9443 (hysteria-udp-proxy.py)
                              │  ← socket bound to en1 IP
                    UDP :443 via WiFi (en1)
                              │
                    VPN server — nebuchadnezzar.fireshare.uk
```

The proxy binds its outgoing sockets to en1's IP. macOS then routes those packets out through en1 regardless of the routing table state, bypassing the soft router entirely.

---

## Setup

### Prerequisites

- macOS 13 Ventura or later
- WiFi connected to the home router (en1 must have an IP before the proxy starts)
- Provisioned files: `mac2.conf`, `mac2-servers.conf`
- Repo cloned at `~/Documents/GitHub/Amnezia-hysteria`

---

### Step 1 — AmneziaWG

Install from the Mac App Store (search **AmneziaWG**), then import `mac2.conf`:

1. AmneziaWG → **+** → **Import tunnel(s) from file** → select `mac2.conf`
2. Click **Allow** when macOS prompts to add a VPN configuration

The config has `Endpoint = 127.0.0.1:1443` — do not change it.

---

### Step 2 — Hysteria2 binary

```bash
mkdir -p ~/bin
ARCH=$(uname -m | sed 's/x86_64/amd64/;s/arm64/arm64/')
curl -L "https://github.com/apernet/hysteria/releases/latest/download/hysteria-darwin-${ARCH}" \
     -o ~/bin/hysteria
chmod +x ~/bin/hysteria
~/bin/hysteria version
```

---

### Step 3 — Config directory and servers.conf

```bash
mkdir -p ~/Library/Application\ Support/hysteria
cp mac2-servers.conf ~/Library/Application\ Support/hysteria/servers.conf
```

Tahoe's servers.conf (tn2 preferred):
```
# ip            region      port
43.160.238.86   singapore   80
8.222.164.32    singapore   80
```

---

### Step 4 — UDP proxy

The proxy binds outgoing sockets to en1. Install it and its LaunchAgent:

```bash
cp ~/Documents/GitHub/Amnezia-hysteria/client/hysteria-udp-proxy.py ~/bin/
chmod +x ~/bin/hysteria-udp-proxy.py

sed "s|REPLACE_USER|$(whoami)|g" \
    ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.hysteria-proxy.plist \
    > ~/Library/LaunchAgents/uk.fireshare.hysteria-proxy.plist

launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria-proxy.plist
```

Verify it started and found en1:
```bash
tail -5 /tmp/hysteria-proxy.log
# Expected: binding remote sockets to en1 (192.168.x.x)
#           proxy up on 127.0.0.1:9443
```

---

### Step 5 — client.yaml

Tahoe's client.yaml points to the proxy (`127.0.0.1:9443`), not directly to the server:

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
    remote: 127.0.0.1:443
    timeout: 0s
EOF
```

---

### Step 6 — Hysteria2 LaunchAgent

```bash
sed "s|<USERNAME>|$(whoami)|g" \
    ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.hysteria.plist \
    > ~/Library/LaunchAgents/uk.fireshare.hysteria.plist

launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria.plist
```

---

### Step 7 — Failover monitor

```bash
cp ~/Documents/GitHub/Amnezia-hysteria/client/hysteria-failover-client.sh \
   ~/bin/hysteria-failover-client.sh
chmod +x ~/bin/hysteria-failover-client.sh

cp ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.hysteria-failover.plist \
   ~/Library/LaunchAgents/

launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria-failover.plist
```

When the failover script switches servers, it restarts the proxy first (to clear stale sessions) and then restarts Hysteria2.

---

### Load order summary

The proxy must be running before Hysteria2 starts. The LaunchAgents load at login in the order they are registered; if you ever need to reload manually:

```bash
launchctl kickstart -k gui/$(id -u)/uk.fireshare.hysteria-proxy
sleep 1
launchctl kickstart -k gui/$(id -u)/uk.fireshare.hysteria
```

---

## Verification

```bash
# 1. Proxy bound to en1
tail -5 /tmp/hysteria-proxy.log

# 2. Hysteria2 connected
tail -20 /tmp/hysteria-mac.log   # look for "connected to server"

# 3. AWG handshake — toggle tunnel off/on in AmneziaWG, wait ~5s, check timestamp in app

# 4. Traffic exits through VPN server (not ISP or soft router)
curl -s https://api.ipify.org   # must return the VPN server IP
```

---

## Troubleshooting

### Proxy log shows no en1 IP

```bash
ipconfig getifaddr en1
```

If blank, WiFi is not connected. Connect en1 to the home router, then restart the proxy:
```bash
launchctl kickstart -k gui/$(id -u)/uk.fireshare.hysteria-proxy
```

The proxy waits up to 60 seconds for en1 to get an IP before giving up.

### Hysteria2 log shows "connection refused" on 127.0.0.1:9443

The proxy is not running. Check:
```bash
launchctl list uk.fireshare.hysteria-proxy
tail -10 /tmp/hysteria-proxy.log
```

### AmneziaWG shows no handshake

1. `launchctl list uk.fireshare.hysteria` — Hysteria2 must be running (PID shown)
2. `netstat -an | grep 1443` — forwarder must be listening
3. `tail -20 /tmp/hysteria-mac.log` — check for connection errors

### curl returns soft router's exit IP instead of VPN server IP

The tunnel is not active. Check the AWG handshake timestamp in the app — if it is stale (> 3 minutes), toggle the tunnel off and on.

### Hysteria2 connects but no data flows through AWG

Check that mac2 has a recent handshake on the server:
```bash
ssh root@43.160.238.86 "awg show awg0"
# Look for mac2's public key — last handshake should be within the last few minutes
```

If handshake is missing, mac2 may not be registered as a peer on that server. Contact the admin.
