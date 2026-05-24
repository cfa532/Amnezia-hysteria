# YouTube Video Switch Latency

## Symptom

On Tahoe, selecting a new YouTube video takes several seconds before it starts loading.

## Cause

Three factors stack up on each new video:

1. **CDN cold start through the tunnel.** The player fetches a manifest and probes bandwidth before buffering. Each request travels: browser → AWG → Hysteria2 → Singapore server → YouTube CDN → back. That is ~150–200ms round-trip per request from the Singapore hop alone, and the player makes several before the first segment loads.

2. **Proxy session timeout.** `hysteria-udp-proxy.py` closes idle sockets after 120 seconds. If more than 2 minutes pass between videos, the proxy session is gone. The next packet triggers new socket creation, a new QUIC stream, and connection setup — adding a visible pause.

3. **YouTube CDN selection based on exit IP.** YouTube picks its CDN node based on the Singapore server's IP. That node may not have the requested video cached, adding an extra round-trip to a primary server.

## Proposed fixes

### 1. AWG `PersistentKeepalive`

Add to the `[Peer]` section in every client `.conf`:

```ini
PersistentKeepalive = 25
```

Sends a keepalive every 25 seconds. The AWG tunnel stays warm and never needs a fresh handshake when a new video starts.

**Catch:** the peer always appears active on the server. It counts toward `active_peers` in the health check even when the client is idle and not sending real traffic. Small continuous battery and bandwidth cost.

### 2. Hysteria2 QUIC keepalive

Add to `client.yaml`:

```yaml
quic:
  keepAlivePeriod: 10s
  maxIdleTimeout: 60s
```

Keeps the QUIC connection to the server alive so it does not need renegotiation between videos.

**Catch:** Hysteria2 holds a connection slot on the server permanently. Small continuous background traffic. Minor resource cost per client.

### 3. Raise proxy socket timeout

Change `settimeout(120)` to `settimeout(300)` in `hysteria-udp-proxy.py`.

Proxy sessions survive typical pauses between videos (2–5 minutes) without closing.

**Catch:** Sockets and file descriptors linger longer. Not a real concern at this scale.

## Decision

Not applied yet. Before making any changes, test whether the pause occurs even when switching videos quickly (within 2 minutes of the previous one finishing). If it does, the tunnels are still warm and the delay is CDN selection — none of the above will help. If the pause only occurs after a longer idle, fixes 2 and 3 are the relevant ones.

CDN selection (cause 3) cannot be fixed from the client side; it is inherent to having a Singapore exit node.
