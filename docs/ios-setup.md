# iOS Client Setup

```
AmneziaWG app ──UDP──▶ nebuchadnezzar.fireshare.uk:443
                                    │
                          VPN server (awg0, AmneziaWG)
                                    │
                               internet
```

iOS connects directly to the server on UDP port 443. There is no local Hysteria2 daemon — it is macOS-only.

---

## Prerequisites

- **AmneziaWG** from the App Store — search "AmneziaWG" and install the app by the Amnezia team. **Do not use the plain WireGuard app.** WireGuard silently ignores the obfuscation fields (Jc, Jmin, H1–H4) and the handshake will always time out.
- Your `iosN.conf` file from the admin (unique to your device).

---

## Step 1 — Get the config onto your iPhone

AirDrop `iosN.conf` from the Mac to your iPhone:

1. On Mac: right-click `iosN.conf` → Share → AirDrop → select your iPhone
2. On iPhone: accept the AirDrop → tap **Open with AmneziaWG**

Alternatively share via iCloud Drive, Messages, or email — any method that lets you open the file with AmneziaWG.

---

## Step 2 — Verify the imported config

In the AmneziaWG app, tap the tunnel → check the Peer section:

| Field | Expected value |
|-------|---------------|
| Endpoint | `nebuchadnezzar.fireshare.uk:443` |
| Public Key | `AQgL8TfJomzJTcNxq/2mhKzgZfOp7eLuFEnsH0PDQhc=` |
| AllowedIPs | `0.0.0.0/0, ::/0` (full) or a long IP list (split) |

If the endpoint shows `127.0.0.1:1443`, delete the tunnel and re-import — that is an old config.

---

## Step 3 — Connect

Toggle the tunnel on in the AmneziaWG app.

---

## Verification

Open Safari and go to `https://api.ipify.org`. The returned IP should be a VPN server IP (tn1 or minipc), not your ISP's IP.

---

## Speed testing

- **Fast.com** — open in Safari, tap start. Simple download test.
- **Speedtest** (Ookla) — free App Store app. Measures download, upload, and ping. Pick a Singapore or Tokyo server for a realistic reading of what the tunnel delivers.
- **YouTube** — play a 4K video. If it sustains 2160p, you have plenty of bandwidth.

Run one test with the tunnel off and one with it on to measure actual overhead.

---

## Routing modes

| Mode | AllowedIPs | Effect |
|------|-----------|--------|
| Full | `0.0.0.0/0, ::/0` | All traffic through VPN |
| Split | Long CIDR list | Chinese IPs bypass VPN; everything else goes through |

Contact the admin to switch modes — this is set at provisioning time.

---

## Troubleshooting

### Handshake always times out

1. **Wrong app** — confirm you are using AmneziaWG, not WireGuard. The WireGuard app won't work.
2. **Old config** — if the endpoint is `127.0.0.1:1443`, delete and re-import the current `iosN.conf`.
3. **Port blocked** — UDP 53 must be open in the cloud security group (Alibaba/Tencent console). Port 53 (DNS) is chosen because it is rarely blocked by home routers or ISPs; if you still time out on Wi-Fi, try cellular to confirm the server is up.

### Connected but no internet

AllowedIPs `0.0.0.0/0` routes everything including DNS through the tunnel. If the tunnel is up but DNS fails, toggle the tunnel off and back on to let the DNS reset.

### Slow on cellular, fast on Wi-Fi

Cellular UDP performance varies by carrier and location. Hysteria2 (used on macOS) is specifically tuned for lossy links; iOS uses raw AWG over UDP 53. This is inherent — no client-side fix.
