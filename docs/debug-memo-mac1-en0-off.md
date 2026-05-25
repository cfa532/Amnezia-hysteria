# Debug Memo: mac1 (Sequoia) fails to activate AWG tunnel when en0 is off

**Date:** 2026-05-25  
**Debugger:** cfa533 @ Tahoe (192.168.5.6) — SSH into mac1 at 192.168.5.8 (user: cfa532)

---

## Problem

When **en0 (wired, soft router) is disabled** and only **en1 (WiFi, 192.168.5.8) is active**, the AmneziaWG tunnel on mac1 fails to activate. It times out silently with no error. Works fine when both en0 and en1 are active.

## What mac1 currently has (matches Tahoe exactly as of this memo)

| Component | Location | State |
|-----------|----------|-------|
| `hysteria-udp-proxy.py` | `~/bin/` | Copied from Tahoe (MD5 matches) |
| `hysteria-failover-client.sh` | `~/bin/` | Copied from Tahoe (MD5 matches) |
| `uk.fireshare.hysteria-proxy.plist` | `~/Library/LaunchAgents/` | KeepAlive, RunAtLoad |
| `uk.fireshare.hysteria.plist` | `~/Library/LaunchAgents/` | KeepAlive, RunAtLoad |
| `uk.fireshare.hysteria-failover.plist` | `~/Library/LaunchAgents/` | StartInterval 120s |
| `uk.fireshare.hysteria-route.plist` | `/Library/LaunchDaemons/` | Runs fix-hysteria-route.sh as root |
| `fix-hysteria-route.sh` | `/usr/local/bin/` | Maintains host routes for server IPs via en1 |
| `client.yaml` | `~/Library/Application Support/hysteria/` | `server: 127.0.0.1:9443` |

AWG tunnel name in AmneziaWG app: **"mac1"** (endpoint: `127.0.0.1:1443`)

## Architecture (should be identical to Tahoe)

```
AmneziaWG "mac1" ──UDP──▶ 127.0.0.1:1443 (Hysteria2 UDP forwarder)
                                  │
                        Hysteria2 client (QUIC)
                                  │
                        127.0.0.1:9443 (hysteria-udp-proxy.py)
                                  │  ← socket explicitly bound to en1 IP
                        UDP :80 via WiFi (en1, 192.168.5.8)
                                  │
                        VPN server (8.222.164.32 or 43.160.238.86)
```

## What works / what doesn't

- **Both en0 + en1 active:** tunnel activates, handshake succeeds, traffic flows ✓
- **Only en1 active (en0 disabled):** tunnel fails silently, no handshake ✗

## Hypothesis

Unknown. The proxy binds to en1 explicitly so routing table changes from en0 going down should not matter. Possible causes to investigate:

1. **Proxy not bound correctly after en0 goes off** — check `/tmp/hysteria-proxy.log` for the en1 IP it's using. If the proxy started while en0 was active, does en1's IP still resolve correctly via `ipconfig getifaddr en1`?

2. **Hysteria2 QUIC connection drops when en0 goes off** — check `/tmp/hysteria-mac.log` for disconnection/reconnection around the time en0 is disabled. If Hysteria2 is mid-reconnect when AWG sends the handshake, it can't forward.

3. **Route-fix daemon removes or corrupts host routes** — check `/tmp/hysteria-route.log`. When en0 goes off, the daemon fires and re-checks routes. Verify `route get 8.222.164.32` still shows `interface: en1` after en0 is disabled.

4. **AWG AllowedIPs races with host routes** — when AWG tunnel activates, it adds a route for `8.0.0.0/7` via utun. The /32 host routes should take precedence, but check if they survive the race.

## Suggested debug steps from Tahoe

SSH to mac1:
```bash
ssh cfa532@192.168.5.8
```

### Step 1 — disable en0 and immediately watch logs
```bash
# In one terminal — watch proxy and hysteria logs live
tail -f /tmp/hysteria-proxy.log /tmp/hysteria-mac.log /tmp/hysteria-route.log &

# Disable en0 from System Settings (or via networksetup):
# sudo networksetup -setnetworkserviceenabled "Ethernet" off
```

### Step 2 — check state after en0 goes off
```bash
ipconfig getifaddr en1                  # must return 192.168.5.8
/sbin/route get 8.222.164.32            # must show interface: en1
/sbin/route get 43.160.238.86           # must show interface: en1
launchctl list | grep fireshare         # all three must have PIDs
tail -5 /tmp/hysteria-proxy.log         # must show "binding remote sockets to en1"
tail -5 /tmp/hysteria-mac.log           # must show "connected to server"
```

### Step 3 — try activating the AWG tunnel and watch
```bash
# Activate the tunnel
scutil --nc start "mac1"
sleep 2
scutil --nc status "mac1" | head -1     # hoping for: Connected

# Check proxy got a new session
tail -5 /tmp/hysteria-proxy.log         # expect: session ... -> server:80 via 192.168.5.8

# Check server side — did the handshake arrive?
ssh -i ~/Documents/GitHub/Amnezia-hysteria/a1-singa.pem root@8.222.164.32 \
    "awg show awg0" | grep -A4 "10.8.0.2"    # mac1's allowed IP
```

### Step 4 — if tunnel still fails, check if proxy is reachable
```bash
# Send a test UDP packet to the proxy and check if it creates a session
echo -n "test" | nc -u -w1 127.0.0.1 9443
tail -3 /tmp/hysteria-proxy.log         # should log a new session attempt
```

## Key files / IPs

| Thing | Value |
|-------|-------|
| mac1 IP | 192.168.5.8 |
| mac1 SSH user | cfa532 |
| Tahoe IP | 192.168.5.6 |
| Server a1-singa | 8.222.164.32 (SSH key: `a1-singa.pem`) |
| Server tn2 | 43.160.238.86 |
| mac1 AWG tunnel IP | 10.8.0.2 |
| mac1 AWG tunnel name (in app) | "mac1" |
