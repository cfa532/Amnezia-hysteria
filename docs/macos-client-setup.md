# Client Setup

Choose the guide that matches your device:

| Document | Who it is for |
|----------|--------------|
| [client-setup.md](client-setup.md) | macOS — regular clients, single physical NIC, one upstream router |
| [dual-nic-setup.md](dual-nic-setup.md) | macOS — dual-NIC machines (mac1/Sequoia, mac2/Tahoe): en0 wired through gen8 soft router, en1 WiFi through home router |
| [ios-setup.md](ios-setup.md) | iPhone / iPad — direct UDP connection |

All clients now connect to AmneziaWG **directly** on UDP 443 (`nebuchadnezzar.fireshare.uk`,
DNS round-robin). The former per-Mac Hysteria2 proxy layer has been retired; macOS
only adds a small `awg-en1-route` daemon to keep the tunnel on the clean interface.
