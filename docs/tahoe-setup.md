# Tahoe (mac2) Setup

Tahoe is a dual-NIC macOS machine (en0 wired → gen8 soft router, en1 WiFi → home
router), identical in setup to mac1 (Sequoia). It connects to AmneziaWG directly
over en1 — the former Hysteria2 proxy layer has been retired.

**Follow [dual-nic-setup.md](dual-nic-setup.md)** and use `mac2.conf` as the
imported config. There are no Tahoe-specific steps.

Quick path:

```bash
# 1. Import mac2.conf into the AmneziaWG app (Endpoint = nebuchadnezzar.fireshare.uk:443)
# 2. Install the en1 route-pinner:
cd ~/Documents/GitHub/Amnezia-hysteria/client
./setup-dual-nic.sh
# 3. Toggle the tunnel on, then verify:
curl -s https://api.ipify.org   # should return tn1 or minipc, not the ISP IP
```

> Tahoe is reachable on either LAN: `192.168.99.6` (en0/gen8) or `192.168.5.6`
> (en1/home WiFi).
