# gen8 Soft Router Setup

gen8 is the home soft router. It connects to minipc via direct AmneziaWG, using split VPN routing for non-China traffic.

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

gen8 connects directly to minipc (not tn1). AWG peer is registered on minipc only.

```
AmneziaWG (wg-tahoe, IP 10.8.1.17) --UDP--> 125.229.161.122:443 (minipc AWG server)
```

- VPN IP: **10.8.1.17/32** (on the 10.8.1.x segment managed by tn1's provisioner)
- Endpoint: **minipc only** (125.229.161.122:443)
- Interface: wg-tahoe via `awg setconf` from `/etc/amnezia/amneziawg/wg-tahoe.setconf`

> **Note:** Hysteria2 QUIC transport was trialled twice (2026-05-30) and rolled back both times.
> See [Lesson: Hysteria2 is worse than direct AWG for cross-strait traffic](#lesson-hysteria2-is-worse-than-direct-awg-for-cross-strait-traffic).
> The Hysteria2 binaries remain at /usr/local/bin/hysteria but services are disabled.

---

## Service management

```sh
# Check tunnel status
sudo awg show wg-tahoe

# Restart tunnel
sudo systemctl restart gen8-awg-tahoe

# Check all VPN services
systemctl status gen8-awg-tahoe gen8-ap-split-vpn gen8-doh-proxy
```

Expected service state:

```text
gen8-awg-tahoe.service       active, enabled   (direct to minipc)
gen8-ap-split-vpn.service    active, enabled
gen8-doh-proxy.service       active, enabled
gen8-hysteria.service        disabled
gen8-hysteria-proxy.service  disabled
```

---

## Peer registration

gen8's peer must be registered **only on minipc**, not tn1:

```sh
# On minipc (via tn1 proxy):
sudo awg set awg0 peer <gen8_pubkey> allowed-ips 10.8.1.17/32
sudo awg-quick save awg0
```

gen8 pubkey: `ClYrDzrD0hSLEJyScK9BxzIgNo6carhxiUGbVeXV7mM=`

---

## Rollback reference

If reconnecting or rebuilding:

1. Ensure `/etc/amnezia/amneziawg/wg-tahoe.setconf` has `Endpoint = 125.229.161.122:443`
2. Ensure gen8's peer is on minipc with `AllowedIPs = 10.8.1.17/32`
3. Ensure gen8's peer is NOT on tn1 (to prevent AWG endpoint roaming to tn1)
4. Restart: `sudo systemctl restart gen8-awg-tahoe`

---

## Lesson: Hysteria2 is worse than direct AWG for cross-strait traffic

**Verdict: keep gen8 on direct AWG (UDP 443) to minipc. Do not tunnel AWG over Hysteria2 on the gen8 (China) → minipc (Taiwan) path.**

Hysteria2 was trialled twice on this path and rolled back both times. The second trial (2026-05-30 night) measured *why* it fails:

| Path | Packet loss | YouTube |
|------|------------|---------|
| Direct AWG (gen8 → minipc:443) | ~15% (tracks the underlying link) | loads in ~2s |
| AWG over Hysteria2 QUIC (port 51820) | ~47% | will not open at all |

**Root cause — QUIC datagram loss amplification.** Hysteria2's `udpForwarding` carries each AWG packet as a QUIC *datagram*, which by design is **not retransmitted** on loss. The cross-strait gen8 → minipc link runs a genuine ~13–15% packet loss (variable, lossy even at night). The QUIC-datagram round trip compounds this to ~47%, which TCP/TLS handshakes cannot survive — so pages fail to open entirely. Direct AWG passes a lost packet straight through at the underlying ~15% rate, which TCP recovers from via normal retransmission.

**Things that did NOT help (ruled out during the trial):**

- **Proxy implementation** — Go and Python UDP proxies performed identically (~5 KB/s through the tunnel). The proxy is never the bottleneck; the QUIC path is.
- **Brutal congestion control** (`bandwidth:` in client config) — made it *worse*. Brutal sends at a fixed rate and never backs off, so when the rate overshoots the real capacity it self-induces congestion loss. 50 Mbps caused near-total collapse; 3–10 Mbps still failed.
- **BBR** (no `bandwidth:`) — collapses to a few KB/s under the path's loss.
- **Larger QUIC receive windows** — irrelevant when the datagrams themselves are being dropped.

minipc's own internet was fine (8.5 MB/s) and minipc load was near zero, confirming the bottleneck is purely the UDP path quality between gen8 and minipc — most likely GFW UDP throttling of port 51820 (the WireGuard default port). Direct AWG on UDP 443 is not throttled the same way.

**When Hysteria2 might still make sense here:** only if the underlying gen8 → minipc link loss is confirmed near-zero *and* the chosen UDP port is not throttled. Diagnose before re-enabling:

```sh
# Compare loss: tunnel path vs direct path
ping -c 20 -I wg-tahoe 8.8.8.8        # through the tunnel
ping -c 20 125.229.161.122            # direct to minipc
```

If the tunnel loss is materially higher than direct, leave Hysteria2 off.

### Companion fix kept regardless of transport: TCP MSS clamp

gen8 forwards LAN clients (192.168.99.0/24) through wg-tahoe (MTU 1280). Without MSS clamping, forwarded TLS silently blackholes — small packets pass but large server responses (e.g. TLS certificates) exceed the tunnel MTU and are dropped, so a site's own `curl` works from gen8 while a LAN client behind it cannot open the same site. The clamp is in `/usr/local/sbin/gen8-ap-split-vpn-up`, placed **before** the forward `accept` rules (a terminating `accept` would skip a later MSS rule):

```sh
nft add rule ip gen8_ap_split_vpn forward oifname wg-tahoe tcp flags syn tcp option maxseg size set 1240
nft add rule ip gen8_ap_split_vpn forward iifname wg-tahoe tcp flags syn tcp option maxseg size set 1240
```

Both directions are required: the outgoing SYN clamps the LAN client's MSS; the returning SYN-ACK clamps the remote server's MSS (the remote has no knowledge of the 1280-byte tunnel). MSS = tunnel MTU − 40 = 1240.
