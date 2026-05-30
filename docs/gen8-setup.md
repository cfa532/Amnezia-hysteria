# gen8 Soft Router Setup

gen8 is the home soft router. It runs AmneziaWG via Hysteria2 transport, identical to the dual-NIC mac client setup, with Linux-specific adaptations.

---

## Network topology

```
Internet / ISP
  -> old home router (192.168.5.x)
       -> gen8 eno1: 192.168.5.2
            -> gen8 soft-router
                 -> eno2 VPN/split LAN: 192.168.99.0/24
                 -> AP clients (Tahoe, Sequoia, etc.)
```

gen8 uses split VPN routing: non-China traffic goes through wg-tahoe (AmneziaWG), China traffic exits via eno1 directly.

---

## Architecture

gen8 uses the same Hysteria2 transport layer as dual-NIC mac clients.

```
AmneziaWG (wg-tahoe) --UDP--> 127.0.0.1:1443 (Hysteria2 UDP forwarder)
                                      |
                            Hysteria2 client (/usr/local/bin/hysteria)
                                      |
                            127.0.0.1:9443 (gen8-hysteria-proxy)
                                      |  <- socket bound to eno1 IP (192.168.5.2)
                            UDP :51820 via eno1
                                      |
                            minipc 125.229.161.122:51820 -- Hysteria2 server
                                      |
                            minipc 127.0.0.1:443 -- AWG server
```

**gen8 connects only to minipc** (not tn1). gen8's AWG peer is registered on minipc only (`ClYrDzrD...`, `10.8.0.35/32`).

The proxy (`gen8-hysteria-proxy`) binds outgoing UDP sockets to eno1's IP. This prevents routing loops: Hysteria2 packets exit via eno1 regardless of the wg-tahoe routing table state.

---

## Key differences from mac dual-NIC setup

| Component | mac (en1) | gen8 (eno1) |
|-----------|-----------|-------------|
| Bind interface | `en1` (WiFi) | `eno1` (wired upstream) |
| IP detection | `ipconfig getifaddr en1` | `ip addr show eno1` |
| Server config | tn1 + minipc in servers.conf | minipc only |
| Service manager | LaunchAgent (plist) | systemd |
| Log location | `/tmp/hysteria-*.log` | `/var/log/gen8-hysteria*.log` |
| AWG tunnel name | `en1` via AmneziaWG app | `wg-tahoe` |

---

## Setup (migration from direct connection)

The setup scripts are in `/home/pi/gen8-hysteria-setup/` on gen8.

### Prerequisites

- gen8 peer must be registered on minipc (`awg show awg0` shows `10.8.0.35/32`)
- gen8 AWG peer (`ClYrDzrD...`) already in minipc's awg0.conf
- GitHub downloads must go through VPN: `sudo chaos curl ...`

### Phase 1 — Install (no disruption to existing tunnel)

```bash
sudo bash /home/pi/gen8-hysteria-setup/install-hysteria.sh
```

This:
1. Downloads Hysteria2 binary via `chaos curl` (routes through wg-tahoe)
2. Installs `gen8-hysteria-proxy` to `/usr/local/sbin/`
3. Creates `/etc/hysteria/config.yaml` and `servers.conf`
4. Installs and starts `gen8-hysteria-proxy.service` and `gen8-hysteria.service`
5. AWG endpoint remains **unchanged**

Verify before proceeding:
```bash
tail -5 /var/log/gen8-hysteria.log        # must show: connected to server
tail -5 /var/log/gen8-hysteria-proxy.log  # must show: binding remote sockets to eno1
```

### Phase 2 — Switch AWG to Hysteria2 (brief disruption ~5s)

```bash
sudo bash /home/pi/gen8-hysteria-setup/switch-to-hysteria.sh
```

This:
1. Verifies Hysteria2 is connected
2. Cleans up decommissioned IPs from `direct-domains.txt`, adds tn1 IP
3. Backs up setconf to `wg-tahoe.setconf.bak-pre-hysteria`
4. Changes AWG endpoint: `125.229.161.122:443` → `127.0.0.1:1443`
5. Restarts `gen8-awg-tahoe.service` and `gen8-ap-split-vpn.service`

Verify:
```bash
# Check AWG handshake on minipc
sudo awg show awg0 | grep -A3 '10.8.0.35'
# Must show: latest handshake within last few minutes
```

### Phase 3 — Finalize (update boot order)

```bash
sudo bash /home/pi/gen8-hysteria-setup/finalize-hysteria.sh
```

Updates `gen8-awg-tahoe.service` to `Requires=gen8-hysteria.service` so boot order is correct.

---

## Service overview

| Service | Description | After |
|---------|-------------|-------|
| `gen8-hysteria-proxy` | UDP proxy, binds eno1 | network.target |
| `gen8-hysteria` | Hysteria2 QUIC client | gen8-hysteria-proxy |
| `gen8-awg-tahoe` | AmneziaWG tunnel | gen8-hysteria |
| `gen8-ap-split-vpn` | Split routing + NAT | gen8-awg-tahoe |

---

## Config files

```
/etc/hysteria/config.yaml           -- Hysteria2 client config
/etc/hysteria/servers.conf          -- minipc only: 125.229.161.122 51820
/usr/local/sbin/gen8-hysteria-proxy -- UDP proxy (binds eno1)
/usr/local/bin/hysteria             -- Hysteria2 binary
/etc/amnezia/amneziawg/wg-tahoe.setconf  -- AWG config (Endpoint = 127.0.0.1:1443)
```

Hysteria2 `config.yaml`:
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
    remote: 127.0.0.1:443
    timeout: 0s
```

---

## Verification

```bash
# Services all active
systemctl is-active gen8-hysteria-proxy gen8-hysteria gen8-awg-tahoe gen8-ap-split-vpn

# Proxy bound to eno1
tail -3 /var/log/gen8-hysteria-proxy.log
# Expected: binding remote sockets to eno1 (192.168.5.2)

# Hysteria2 connected
tail -3 /var/log/gen8-hysteria.log
# Expected: connected to server

# AWG handshake on minipc (from mac via SSH)
sshpass -p 'builder' ssh -tt -p 220 pi@125.229.161.122 \
  "echo 'builder' | sudo -S awg show awg0 | grep -A3 '10.8.0.35'"
# Expected: latest handshake: X seconds ago
```

---

## Rollback

If Hysteria2 is unavailable, revert AWG to direct minipc connection:

```bash
sudo bash /home/pi/gen8-hysteria-setup/rollback-to-direct.sh
```

This restores `Endpoint = 125.229.161.122:443` from backup and restarts the tunnel.

---

## Troubleshooting

**AWG has no handshake after restart:**
1. Verify Hysteria2 connected: `tail /var/log/gen8-hysteria.log`
2. Restart Hysteria2 stack in order:
   ```bash
   sudo systemctl restart gen8-hysteria-proxy.service
   sleep 2
   sudo systemctl restart gen8-hysteria.service
   sleep 3
   sudo systemctl restart gen8-awg-tahoe.service gen8-ap-split-vpn.service
   ```

**GitHub downloads fail from gen8:**
Always route GitHub downloads through VPN:
```bash
sudo chaos curl -L <url> -o /tmp/filename
# Then move as root: sudo install -m 755 /tmp/filename /usr/local/bin/filename
```

**Cannot SSH to gen8 via AWG tunnel:**
gen8 is reachable at `10.8.0.35:22` from any device with an active AWG tunnel.
From minipc: `ssh pi@10.8.0.35`

**VPN watchdog interaction:**
`gen8-vpn-watchdog` checks `gen8-check-split-vpn` every 5 minutes. After migration,
the health check's "Expected VPN egress: 125.229.161.122" still passes because
Hysteria2 routes through minipc, whose exit IP is the same.
