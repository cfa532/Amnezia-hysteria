# Dual-NIC macOS Setup

This guide covers any macOS machine with **two physical network interfaces** where:

- **en0** (wired) connects through a soft router that runs its own VPN
- **en1** (WiFi) connects directly to the home router — the path used for VPN testing

Machines with this topology: **mac1 (Sequoia)**, **mac2 (Tahoe)**.

---

## Network topology

| Interface | Connected to | Used for VPN |
|-----------|-------------|-------------|
| en0 (wired) | Soft router (built-in VPN) | No — traffic gets re-wrapped |
| en1 (WiFi) | Home router | Yes — clean path to the internet |

All Hysteria2 traffic must leave through **en1**.

---

## Why the regular setup is not enough

Regular macOS clients prevent routing loops with host routes: permanent `/32` routes for the server IPs that point at the physical gateway instead of the tunnel interface. The route-fix daemon (`uk.fireshare.hysteria-route`) maintains these routes and is installed on dual-NIC machines too.

Host routes work correctly under normal conditions. However, on macOS 26 with two active "interfaces" (en1 + the AWG utun), the kernel's UDP source-address selection does not reliably honour the host routes when choosing a source IP for outgoing QUIC packets. Observed behaviour:

- With **both en0 and en1 active**: source-address selection happens to pick en1 correctly — VPN works.
- With **only en1 active** (en0 disabled or unplugged) **and AWG tunnel active**: macOS sometimes picks the wrong source address, the QUIC handshake to the server fails, and AWG times out waiting for a response.

The fix is to bind the outgoing socket to en1's IP explicitly, bypassing source-address selection entirely.

---

## Architecture

An extra component sits between Hysteria2 and the server: `hysteria-udp-proxy.py`.

```
AmneziaWG app ──UDP──▶ 127.0.0.1:1443 (Hysteria2 UDP forwarder)
                              │
                    Hysteria2 client (QUIC)
                              │
                    127.0.0.1:9443 (hysteria-udp-proxy.py)
                              │  ← outgoing socket bound to en1 IP
                    UDP :443 via WiFi (en1)
                              │
                    VPN server — nebuchadnezzar.fireshare.uk
```

The proxy reads the current server from `servers.conf` and creates one UDP socket per session, bound to en1's IP. macOS routes those packets out through en1 regardless of routing table state.

---

## Setup

### Prerequisites

- macOS 13 Ventura or later
- WiFi connected to the home router (en1 must have an IP before the proxy starts)
- Provisioned files: `macN.conf`, `macN-servers.conf` in `~/Documents/Gen8/`
- Repo cloned at `~/Documents/GitHub/Amnezia-hysteria`

### Step 0 — Run the setup script

One script installs and configures everything identically on any dual-NIC machine:

```bash
cd ~/Documents/GitHub/Amnezia-hysteria/client
./setup-dual-nic.sh mac1   # replace mac1 with your device name
```

The script installs the Hysteria2 binary, UDP proxy, failover monitor, and route-fix daemon; writes `client.yaml` in proxy mode; and loads all LaunchAgents. It requires `sudo` once for the route-fix daemon.

If the script succeeds, skip to [Verification](#verification). The manual steps below are for reference only.

---

### Step 1 — AmneziaWG

Install from the Mac App Store (search **AmneziaWG**), then import your `macN.conf`:

1. AmneziaWG → **+** → **Import tunnel(s) from file** → select `macN.conf`
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
cp macN-servers.conf ~/Library/Application\ Support/hysteria/servers.conf
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

Point Hysteria2 at the proxy, not directly at the server:

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

### Step 6 — Route-fix daemon

Maintains host routes for the server IPs so they always exit via en1, not the AWG tunnel. Requires `sudo`.

```bash
sudo cp ~/Documents/GitHub/Amnezia-hysteria/client/fix-hysteria-route.sh \
     /usr/local/bin/fix-hysteria-route.sh
sudo chmod +x /usr/local/bin/fix-hysteria-route.sh

sudo cp ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.hysteria-route.plist \
     /Library/LaunchDaemons/

sudo launchctl bootstrap system \
     /Library/LaunchDaemons/uk.fireshare.hysteria-route.plist
```

Verify:
```bash
route get 43.165.128.251    # interface must be en1, NOT utunX
route get 125.229.161.122
```

---

### Step 7 — Hysteria2 LaunchAgent

```bash
sed "s|<USERNAME>|$(whoami)|g" \
    ~/Documents/GitHub/Amnezia-hysteria/client/uk.fireshare.hysteria.plist \
    > ~/Library/LaunchAgents/uk.fireshare.hysteria.plist

launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria.plist
```

---

### Step 8 — Failover monitor

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

The proxy must be running before Hysteria2 starts:

```bash
launchctl kickstart -k gui/$(id -u)/uk.fireshare.hysteria-proxy
sleep 1
launchctl kickstart -k gui/$(id -u)/uk.fireshare.hysteria
```

---

## Split routing

Your provisioned `macN.conf` has `AllowedIPs` set to the full non-China CIDR list: Chinese IP ranges route direct via the ISP; all other traffic goes through the VPN.

The route-fix daemon (`fix-hysteria-route.sh`) reads `servers.conf` at runtime and installs `/32` host routes for every server IP via en1. This means server IPs do not need to be excluded from `AllowedIPs` — the `/32` routes take precedence over any matching CIDR block. If the server list changes, the daemon picks up the new IPs on next restart.

The current macOS AllowedIPs list is maintained at `/etc/vpn-controller/split-allowed-ips.txt` on tn1. To update after a China routing table change:
1. Update `split-allowed-ips.txt` on tn1
2. Pull the updated list into your `macN.conf` and re-import in the AmneziaWG app

If provisioned with `routing=full`, `AllowedIPs = 0.0.0.0/0, ::/0` and all traffic goes through the VPN. Contact the admin to switch to split routing.

---

## Verification

```bash
# 1. Proxy bound to en1
tail -5 /tmp/hysteria-proxy.log

# 2. Hysteria2 connected via proxy
tail -20 /tmp/hysteria-mac.log   # look for: connected to server {"addr": "127.0.0.1:9443", ...}

# 3. AWG handshake — toggle tunnel off/on in AmneziaWG, wait ~5s, check timestamp in app

# 4. Traffic exits through VPN server
curl -s https://api.ipify.org   # must return the VPN server IP, not your ISP IP
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

1. `launchctl list uk.fireshare.hysteria` — Hysteria2 must show a PID
2. `netstat -an | grep 1443` — forwarder must be listening
3. `tail -20 /tmp/hysteria-mac.log` — check for connection errors

### AWG connects but curl returns ISP IP

The route-fix daemon may not be running or the routes are stale:
```bash
sudo launchctl list uk.fireshare.hysteria-route
route get 43.165.128.251   # must NOT show utunX
```

### Hysteria2 connects but no data flows through AWG

Check that your device has a recent handshake on the server:
```bash
# SSH to whichever server your proxy selected (check /tmp/hysteria-server-index)
sshpass -p '<password>' ssh root@43.165.128.251 "awg show awg0"
# Look for your device's allowed IP — last handshake should be within the last few minutes
```

If handshake is missing, the device may not be registered as a peer on that server. Contact the admin.
