# Hysteria2 — Legacy Client How-To (retired)

> **Status: retired from the client path.** macOS clients no longer tunnel
> AmneziaWG over a local Hysteria2 proxy — they connect to AWG directly on UDP 443
> (`nebuchadnezzar.fireshare.uk`). gen8 also runs direct AWG. The Hysteria2 server
> (`hysteria.service` on UDP 51820) is still installed on tn1/minipc but unused,
> and will be removed later.
>
> This note preserves the Hysteria2 client setup for reference and in case the
> transport is ever needed again on a path where it helps.

---

## When Hysteria2 helps (and when it hurts)

Hysteria2 wraps AWG in **QUIC** to defeat GFW **TCP throttling** on otherwise
clean paths — it measurably beat wstunnel there (≈8 KB/s → 750 KB/s–3.4 MB/s).

It **hurts** when the underlying path already has real packet loss. Its
`udpForwarding` carries each AWG packet as a QUIC *datagram*, which is **not
retransmitted**. On the cross-strait gen8 → minipc link (~15% loss) the loss
compounded to ~47% and TCP became unusable — *worse* than direct AWG. That is why
it was retired here. Always compare tunnel-path vs direct-path loss before
choosing Hysteria2 on a new link (see
[gen8-setup.md](gen8-setup.md#lesson-hysteria2-is-worse-than-direct-awg-for-cross-strait-traffic)).

---

## Architecture (when it was active)

```
AmneziaWG (utun) ──UDP──▶ 127.0.0.1:1443 (Hysteria2 UDP forwarder)
                                │
                      Hysteria2 client (QUIC)
                                │
                      127.0.0.1:9443 (hysteria-udp-proxy.py)   ← dual-NIC only
                                │  socket bound to en1
                      UDP :51820 via en1 ──▶ server (QUIC, masquerades as HTTPS)
                                │
                      server awg0 (AmneziaWG) → internet
```

On dual-NIC machines an extra `hysteria-udp-proxy.py` sat between the Hysteria2
client and the server to bind the outgoing socket to en1 (macOS source-address
selection couldn't be trusted to pick en1 otherwise). Single-NIC machines pointed
the Hysteria2 client straight at the server and used a route-fix daemon instead.

---

## Server setup

Install the binary and run `hysteria server`:

```bash
curl -fsSL https://github.com/apernet/hysteria/releases/latest/download/hysteria-linux-amd64 \
  -o /usr/local/bin/hysteria
chmod +x /usr/local/bin/hysteria
```

`/etc/hysteria/server.yaml`:
```yaml
listen: :51820

tls:
  cert: /etc/ssl/<domain>/fullchain.pem
  key:  /etc/ssl/<domain>/key.pem

auth:
  type: password
  password: <YOUR_AUTH_PASSWORD>

quic:
  initStreamReceiveWindow: 26843545
  maxStreamReceiveWindow:  26843545
  initConnReceiveWindow:   67108864
  maxConnReceiveWindow:    67108864

bandwidth:        # Brutal CC; set to real link capacity, NOT higher (see caveat)
  up: 1 gbps
  down: 1 gbps

masquerade:       # unauthenticated HTTPS gets a real proxy response
  type: proxy
  proxy:
    url: https://www.bing.com/
    rewriteHost: true
```

`systemctl enable --now hysteria`. The server presents the same identity as AWG
(shared keypair) so DNS round-robin is transparent.

> **Brutal-mode caveat:** `bandwidth:` enables Brutal congestion control, which
> sends at a *fixed* rate and never backs off. Set above the real path capacity it
> self-induces loss and collapses. Omit `bandwidth:` to fall back to BBR (adaptive,
> but it too collapses under heavy loss). On a lossy path, neither saves you —
> use direct AWG instead.

---

## macOS client setup (proxy mode, dual-NIC)

### 1. Binary
```bash
mkdir -p ~/bin
ARCH=$(uname -m | sed 's/x86_64/amd64/;s/arm64/arm64/')
curl -fsSL "https://github.com/apernet/hysteria/releases/latest/download/hysteria-darwin-${ARCH}" -o ~/bin/hysteria
chmod +x ~/bin/hysteria
```

### 2. servers.conf + client.yaml
```bash
mkdir -p ~/Library/Application\ Support/hysteria
cp config/servers.conf ~/Library/Application\ Support/hysteria/servers.conf
```
`~/Library/Application Support/hysteria/client.yaml` (proxy mode — points at the
local UDP proxy, not the server):
```yaml
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
```
(Single-NIC: set `server:` to the real server `IP:51820` and skip the proxy.)

### 3. UDP proxy (dual-NIC, binds en1)
```bash
cp client/hysteria-udp-proxy.py ~/bin/
chmod +x ~/bin/hysteria-udp-proxy.py
sed "s|REPLACE_USER|$(whoami)|g" client/uk.fireshare.hysteria-proxy.plist \
  > ~/Library/LaunchAgents/uk.fireshare.hysteria-proxy.plist
launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria-proxy.plist
```
A Go rewrite of the proxy lives in `client/hysteria-proxy/` (faster, but the proxy
was never the bottleneck — the QUIC path was).

### 4. LaunchAgents
```bash
sed "s|<USERNAME>|$(whoami)|g" client/uk.fireshare.hysteria.plist \
  > ~/Library/LaunchAgents/uk.fireshare.hysteria.plist
launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria.plist

cp client/hysteria-failover-client.sh ~/bin/
cp client/uk.fireshare.hysteria-failover.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/uk.fireshare.hysteria-failover.plist
```

### 5. AmneziaWG profile
Endpoint must point at the local forwarder, and the server IPs must be **excluded**
from `AllowedIPs` (else Hysteria2's own QUIC packets get captured → loop):
```ini
[Peer]
Endpoint = 127.0.0.1:1443
AllowedIPs = <split list with every server /32 excluded>
```
Plus a route-fix daemon to keep the server IPs off utun. (The current direct-AWG
setup uses the renamed `awg-en1-route` daemon for the same purpose — see
[dual-nic-setup.md](dual-nic-setup.md).)

---

## Re-enabling / disabling

The repo still contains the client pieces: `client/hysteria-udp-proxy.py`,
`client/hysteria-proxy/` (Go), `client/hysteria-client-device*.yaml`,
`client/hysteria-failover-client.sh`, and the `uk.fireshare.hysteria*.plist`
LaunchAgents. The `setup-dual-nic.sh` script no longer installs them (it installs
only the direct-AWG route-pinner and removes leftover Hysteria agents).

To fully decommission on the servers: `systemctl disable --now hysteria` on tn1
and minipc, and drop UDP 51820 from their security groups.
