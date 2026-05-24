# macOS Client Setup — Direct AmneziaWG

This guide covers connecting a macOS device to the VPN using direct AmneziaWG (no Hysteria2). This is the recommended method for all macOS clients that are not Sequoia or Tahoe (which use the Hysteria2 transport for higher throughput).

---

## Requirements

- macOS 13 Ventura or later
- AmneziaWG app (free)
- A config file (`macN.conf`) provisioned by the server admin

---

## Step 1 — Install AmneziaWG

Download from the Mac App Store: search **AmneziaWG**.

> AmneziaWG is a drop-in replacement for the standard WireGuard app. It adds obfuscation to prevent DPI fingerprinting by firewalls.

---

## Step 2 — Get Your Config File

The server admin will provide a `.conf` file (e.g. `mac1.conf`). It looks like this:

```ini
[Interface]
PrivateKey = <your-private-key>
Address = 10.8.0.2/32
DNS = 8.8.8.8, 1.1.1.1
MTU = 1280

Jc = 4
Jmin = 40
Jmax = 70
S1 = 30
S2 = 40
S3 = 30
S4 = 40
H1 = 11223
H2 = 44556
H3 = 77889
H4 = 99001

[Peer]
PublicKey = <server-public-key>
Endpoint = <vpn-domain>:443
AllowedIPs = 0.0.0.0/1, 128.0.0.0/1, ::/1, 8000::/1
PersistentKeepalive = 25
```

**Each config file is unique to one device.** Do not share or reuse config files — two devices using the same file will knock each other offline every 25 seconds.

---

## Step 3 — Import the Config

1. Open **AmneziaWG**
2. Click the **+** button (bottom left)
3. Choose **Import tunnel(s) from file**
4. Select your `.conf` file
5. Click **Allow** when macOS asks to add VPN configurations

---

## Step 4 — Connect

Toggle the tunnel on in the AmneziaWG app.

To verify the tunnel is working:

```bash
# Find your tunnel interface (look for inet 10.8.0.x)
ifconfig | grep -B1 'inet 10.8.0'

# Check your exit IP — should be the VPN server IP
curl --interface <utunX> -s https://api.ipify.org
```

A successful connection returns the VPN server's public IP, not your ISP's IP.

---

## Troubleshooting

### Tunnel connects but no traffic flows

Check that the tunnel interface exists:
```bash
ifconfig | grep 'inet 10.8.0'
```
If the address appears but curl fails, try flushing DNS:
```bash
sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder
```

### Tunnel never establishes (no handshake)

The ISP may be blocking the port. This VPN requires **UDP 443** to reach the server. Ports like 51820 or 51821 are commonly blocked in China.

Confirm by checking from the server side:
```bash
awg show awg0   # should show 'latest handshake' for your peer after connecting
```

### Conflict with soft router / existing VPN

If your Mac is behind a soft router that runs its own VPN, the soft router may intercept or NAT UDP 443 before it reaches the VPN server. Options:
- Add the VPN server's IP to the soft router's bypass list (direct ISP routing for that IP)
- Connect via a network that doesn't go through the soft router (e.g. mobile hotspot or a different Wi-Fi)

### macOS 26.x — tunnel appears connected but curl hangs

This is a known macOS 26.5 bug. The config already works around it by using split-route `AllowedIPs` instead of `0.0.0.0/0`. If you see this with a config that uses `0.0.0.0/0`, replace it with:
```ini
AllowedIPs = 0.0.0.0/1, 128.0.0.0/1, ::/1, 8000::/1
```

---

## Notes

- **AllowedIPs = full tunnel**: all traffic (including Chinese sites) exits through the VPN server. This is intentional for now — split-tunnel by country is a future feature.
- **DNS**: the config uses `8.8.8.8, 1.1.1.1` via the tunnel. If you prefer a different DNS, edit the `DNS =` line before importing.
- **Failover**: if the VPN server is unreachable, toggle the tunnel off and back on. DNS round-robin may route you to a different healthy server on reconnect. Automated failover is on the roadmap.
