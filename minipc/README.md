# minipc host protection

minipc is the home-router-backed AmneziaWG server. The router's existing SSH
port-forward remains in place, while Fail2ban protects the host's local SSH
listener on TCP 22.

- Repository jail: [`fail2ban-sshd.local`](fail2ban-sshd.local)
- Installed jail: `/etc/fail2ban/jail.d/sshd.local`
- Shared policy and operations: [`../docs/ssh-protection.md`](../docs/ssh-protection.md)

The `10.8.x.x` AmneziaWG networks are VPN data-plane addresses and are not
management networks or Fail2ban exemptions.
