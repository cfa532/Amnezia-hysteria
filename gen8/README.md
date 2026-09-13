# gen8 soft-router — split-CIDR generator

Reference copies of the scripts that run **on the gen8 soft router** (not deployed
by this repo's setup). Captured here for posterity; see
[../docs/gen8-setup.md](../docs/gen8-setup.md) for gen8's full role.

- `gen8-update-split-cidrs.py` — fetches the APNIC delegated-stats table, extracts
  CN IPv4 allocations, computes the non-China complement, and (with `--apply`)
  writes it into gen8's `wg-tahoe.conf`/`.setconf` `AllowedIPs`, restarts the
  tunnel + split-VPN, health-checks, and rolls back on failure. Installed at
  `/usr/local/sbin/gen8-update-split-cidrs`.
- `gen8-update-split-cidrs.service` / `.timer` — weekly run (Sun 04:20).

This APNIC complement remains specific to gen8's router policy. Client profiles
use a different tiered strategy: the controller's
[`update-china-firm-bypass.py`](../controller/update-china-firm-bypass.py)
maintains major-firm and priority QR datasets. Mobile file imports are capped at
32 KiB, QR profiles at 2000 bytes, and desktop file imports retain the full set;
generated `AllowedIPs` sends all other public IPv4 through AWG.

## FEC layer (cross-strait packet-loss mitigation)

The gen8 → minipc link is lossy; an FEC tunnel (UDPspeeder) recovers the loss and
~2–3× throughput. See [../docs/gen8-fec.md](../docs/gen8-fec.md).

- `udpspeeder-gen8.service` — FEC **server**, runs on **minipc** (UDP 80 → local AWG 443)
- `udpspeeder.service` — FEC **client**, runs on **gen8** (127.0.0.1:4000 → minipc:80)
- `gen8-fec-rollback` — revert gen8 to direct AWG (`/usr/local/sbin/` on gen8)

Binary is `speederv2_amd64` from the wangyu-/UDPspeeder release (x86_64 both ends).
