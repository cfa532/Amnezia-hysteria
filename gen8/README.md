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

This is the **canonical full-list generator**. The controller runs the same
algorithm (minus the wg-tahoe apply step) via
[`../controller/update-split-full-list.py`](../controller/update-split-full-list.py)
to keep tn1's macOS provisioning list
(`/etc/vpn-controller/split-allowed-ips-full.txt`) on the identical APNIC-derived
set. Both currently yield ~11985 non-China IPv4 routes.
