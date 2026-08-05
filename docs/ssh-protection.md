# SSH protection — gen8 and minipc

**Deployed:** 2026-08-05

Both hosts run Fail2ban 1.0.2 with the `sshd` jail enabled. Fail2ban reads the
systemd journal and adds temporary firewall bans for repeated authentication
failures. Existing SSH listeners, router forwarding, and nftables routing rules
are unchanged.

## Ban policy

| Setting | Value |
|---|---:|
| Failed attempts | 5 |
| Detection window | 10 minutes |
| Initial ban | 1 hour |
| Repeat-offender maximum | 1 day |

Repeat bans increase in duration. Loopback and the documented trusted
management sources are exempt so an automated controller or local administrator
cannot be banned by an expired key or typing error.

## gen8

gen8 is directly internet-facing on `ppp0`. Its pre-existing
`gen8_ssh_guard` nftables table remains the first layer of protection:

- WAN TCP 22 is dropped unconditionally.
- WAN TCP 220 is rate-limited per source.
- Trusted LAN access is allowed.

Fail2ban is the authentication-aware second layer and covers both local SSH
listeners, TCP 22 and TCP 220. Trusted sources are loopback,
`192.168.1.0/24`, `192.168.99.0/24`, and the current Tailscale management
address.

Repository configuration: [`../gen8/fail2ban-sshd.local`](../gen8/fail2ban-sshd.local)

## minipc

minipc is behind its home router. The router's existing SSH port-forward is
unchanged. On minipc itself, `sshd` receives connections on local TCP 22, so
that is the port protected by the jail; the router's public-side port does not
belong in the Fail2ban configuration.

Trusted sources are loopback, the minipc LAN (`192.168.5.0/24`), the current
Tailscale management address, and the Av1 controller (`47.245.61.67`). The
`10.8.x.x` AmneziaWG data plane is deliberately not treated as a management
network.

Repository configuration: [`../minipc/fail2ban-sshd.local`](../minipc/fail2ban-sshd.local)

## Deployment and operations

The host-specific file is installed as:

```text
/etc/fail2ban/jail.d/sshd.local
```

Service and jail status:

```sh
systemctl status fail2ban --no-pager
sudo fail2ban-client status sshd
```

Inspect service logs:

```sh
sudo journalctl -u fail2ban --no-pager
```

Remove an accidental ban:

```sh
sudo fail2ban-client set sshd unbanip <IP_ADDRESS>
```

If a LAN, controller, or Tailscale management address changes, update
`ignoreip` in the corresponding repository file and deployed jail before the
old trusted path is retired.
