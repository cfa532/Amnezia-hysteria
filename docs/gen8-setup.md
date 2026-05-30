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

> **Note:** Hysteria2 QUIC transport was trialled (2026-05-30) but rolled back due to ~8 Mbps throughput.
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
