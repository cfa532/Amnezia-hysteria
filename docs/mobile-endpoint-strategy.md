# Mobile Endpoint Strategy

**Status:** Proposed
**Date:** 2026-07-09
**Scope:** iOS and Android AmneziaWG clients

---

## Problem

Mobile clients sometimes fail silently after a short network disconnect. On iOS,
an immediate reconnect can fail repeatedly, while reconnecting after a waiting
period succeeds. This behaves like a client-side cooldown.

The current mobile profile already uses the known stability settings:

- Reduced mobile split list
- `MTU = 1180`
- IPv4 DNS only
- `PersistentKeepalive = 10`
- Active server IPs excluded from `AllowedIPs`

Because those settings are already in place, the remaining failure mode is
unlikely to be ordinary server response latency. The more likely causes are iOS
NetworkExtension path state, carrier NAT state, DNS cache state, or endpoint
selection changing underneath the same tunnel profile.

---

## Current Model: DNS Round-Robin

Mobile clients use:

```ini
Endpoint = nebuchadnezzar.fireshare.uk:443
```

Cloudflare returns one or more A records, currently:

- `47.245.61.67` (`av1`)
- `125.229.161.122` (`minipc`)

All servers share the same AmneziaWG server keypair and carry the same mobile
peer list, so any returned backend can accept the same client config.

### Benefits

- One config works across all healthy servers.
- A dead backend can be removed from DNS by the controller.
- No client reprovisioning is needed for normal backend failover.
- This is operationally simple for a small fleet.

### Problems On Mobile

- iOS may cache DNS or path state longer than the operator expects.
- Immediate retries can reuse the same failed endpoint/path state.
- DNS round-robin can move a reconnect attempt to a different backend while iOS
  is still recovering the previous tunnel state.
- TTL is advisory; mobile OS and carrier behavior may not respect it cleanly.
- There is no mobile equivalent of the macOS route-pinner.
- When a new backend IP falls inside the mobile `AllowedIPs` coverage, every
  mobile config must be regenerated to exclude it.

The result is elegant failover on paper, but less predictable reconnect behavior
on iOS.

---

## Option 1: Keep DNS Round-Robin For Mobile

### Description

Continue provisioning all iOS and Android clients with the shared DNS endpoint.

```ini
Endpoint = nebuchadnezzar.fireshare.uk:443
```

### Pros

- Lowest provisioning complexity.
- One profile per device.
- Backend health changes are handled centrally through DNS.
- Works well when mobile OS DNS/path state behaves normally.
- No explicit per-client server assignment to manage.

### Cons

- Does not directly address the iOS cooldown behavior.
- Reconnect attempts can hit stale DNS, stale path, or stale carrier NAT state.
- DNS TTL does not guarantee mobile retry timing.
- Harder to reason about user reports because the backend may vary between
  attempts.
- Adding servers still requires careful mobile `AllowedIPs` exclusion checks.

### Fit

Acceptable for small fleets or for clients where occasional manual retry delay is
tolerable. Not ideal for a large iOS-heavy deployment.

---

## Option 2: Manual Pinned Profiles

### Description

Provision profiles with a fixed server IP:

```ini
Endpoint = 47.245.61.67:443
```

For backup, a user may have two profiles with separate device names, keys, and
VPN IPs:

```text
ios1-av1    -> 47.245.61.67:443
ios1-minipc -> 125.229.161.122:443
```

Only one should be active at a time.

### Pros

- Good diagnostic test for whether DNS round-robin contributes to cooldown.
- Removes DNS lookup and backend selection from the retry path.
- User can switch to a backup profile if the primary path is stuck.
- Simple to implement with the current provisioning API.

### Cons

- Bad user experience at scale.
- Users must understand which profile to use.
- Manual failover does not scale to many non-technical users.
- More profiles means more keys, more peer rows, and more support surface.
- Load distribution depends on provisioning discipline.

### Fit

Useful for debugging, admins, and small trusted groups. Not recommended as the
main model for a large customer/user fleet.

---

## Option 3: Controller-Managed Sticky Endpoint

### Description

At provisioning time, the controller selects a healthy server by region and load,
then writes that server IP directly into the mobile config:

```ini
Endpoint = 47.245.61.67:443
```

The assignment is sticky. Existing clients keep their assigned endpoint until the
controller intentionally migrates them.

New mobile clients are still load-balanced, but load balancing happens once at
provisioning time instead of on every reconnect.

### Pros

- Removes DNS round-robin from the iOS reconnect path.
- Preserves fleet-level load balancing for new clients.
- Reconnect behavior is easier to debug because each client has a known primary.
- Avoids exposing ordinary users to multiple manual profiles.
- Server migrations can be planned and rolled out deliberately.
- Works with the existing peer-on-all-servers design.

### Cons

- A dead primary server no longer fails over automatically through DNS.
- Moving a client requires reprovisioning or a profile update.
- The controller must track assigned endpoint, not just assigned region.
- Capacity planning must consider sticky clients, not only active sessions.
- Long-lived assignments can become imbalanced unless periodically reviewed.

### Fit

Best near-term production model for a large iOS/Android fleet. It trades fully
automatic DNS failover for predictable mobile reconnect behavior and simpler
support.

---

## Option 4: Stable Regional Relay Or VIP

### Description

Expose one stable mobile endpoint per region, then route or forward behind it:

```ini
Endpoint = asia-mobile.example.com:443
```

The relay/VIP remains stable for clients. Backend server changes happen behind
that stable endpoint.

Possible implementations:

- A small relay VPS with a safe IP outside the mobile `AllowedIPs` coverage
- A UDP-capable cloud load balancer
- A regional anycast or floating-IP design
- A custom relay that forwards AmneziaWG UDP packets to selected backends

### Pros

- Best client experience: one stable endpoint, no client churn.
- Backend migrations do not require mobile reprovisioning.
- Can keep health checks and failover server-side.
- Avoids DNS round-robin behavior on iOS.
- Reduces the mobile `AllowedIPs` exclusion problem if the relay IP is stable and
  known-safe.

### Cons

- More infrastructure to operate.
- Adds latency and one more network dependency.
- Relay becomes critical path unless deployed redundantly.
- UDP load balancing must preserve enough flow affinity for WireGuard/AmneziaWG.
- Debugging becomes more layered.

### Fit

Best long-term model for a large commercial deployment, especially across
regions. More work than sticky assignment, but cleaner for users.

---

## Recommendation

Use a staged approach.

### Phase 1: Validate The Hypothesis

Generate pinned iOS profiles for a small number of affected devices.

```bash
ENDPOINT=preferred PROVISION_TOKEN='<BEARER_TOKEN>' \
  ./reprovision.sh ios1 ios split /tmp
```

If immediate reconnect works better with a pinned endpoint, DNS/backend selection
is part of the cooldown problem.

### Phase 2: Adopt Sticky Endpoint For Mobile

For iOS and Android, the default provisioning policy is now:

- Select server by region and load at provisioning time.
- Write the selected server IP into `Endpoint`.
- Store `preferred_server`, `endpoint`, and `endpoint_policy` in controller state.
- Preserve an existing mobile client's sticky server on reprovision when it is
  still healthy and available.
- Keep registering each peer on all servers so emergency migration remains
  possible.
- Keep DNS round-robin for macOS, where the route-pinner makes it safer.

This is the best balance between scale, simplicity, and iOS stability.

### Phase 3: Add Managed Migration

Add controller operations to move clients between endpoints:

- List clients by assigned endpoint.
- Select a migration batch.
- Generate updated configs or QR codes.
- Mark old profile/key inactive after migration.
- Track migration completion.

This keeps sticky assignment from turning into permanent imbalance.

### Phase 4: Build A Stable Regional Endpoint

If the user base grows enough that reprovisioning is painful, introduce a stable
regional mobile endpoint. This can be a relay/VIP layer in front of backend AWG
servers.

---

## Operational Policy

### Mobile Defaults

For iOS/Android production profiles:

```ini
DNS = 8.8.8.8
MTU = 1180
PersistentKeepalive = 10
Endpoint = <assigned-server-ip>:443
```

Use the reduced mobile split list and keep active endpoint IPs outside
`AllowedIPs`.

### macOS Defaults

macOS can continue using DNS round-robin:

```ini
Endpoint = nebuchadnezzar.fireshare.uk:443
```

The `awg-en1-route` daemon pins resolved endpoint IPs to the physical gateway and
can adapt to DNS changes.

### Capacity Management

For sticky mobile clients, track both:

- Active peers: current load signal from `awg show awg0 dump`
- Assigned clients: long-term capacity signal from controller state

Provisioning should consider both numbers. A server with low active peers but a
large assigned-client population may still be overloaded during peak hours.

### Failure Handling

For a failed mobile endpoint:

1. Remove the server from new-client assignment.
2. Keep existing peers registered on other servers.
3. Generate replacement profiles for affected mobile clients.
4. Prefer batch migration over forcing every client through DNS failover.

For admins and power users, dual pinned profiles remain useful:

```text
primary profile -> assigned server
backup profile  -> alternate server
```

Only one profile should be active at a time.

---

## Implementation Notes

The provisioning API supports an `endpoint` request field:

```json
{
  "device_name": "ios1",
  "os_type": "ios",
  "routing": "split",
  "endpoint": "auto"
}
```

Supported values:

| Value | Behavior |
|-------|----------|
| `auto` | iOS/Android use the selected sticky server IP; macOS uses DNS |
| `dns` | Use `nebuchadnezzar.fireshare.uk:443` |
| `preferred` | Use the selected healthy server IP |
| `IP[:PORT]` | Use an explicit endpoint |
| `host[:PORT]` | Use an explicit hostname |

The generated endpoint is stored in controller client state and returned in the
provisioning response. `reprovision.sh` defaults to `ENDPOINT=auto`.

---

## Decision

For a large number of mobile clients, do not rely on manual pinned profiles as
the main user experience.

Adopt **controller-managed sticky mobile endpoints** as the near-term production
design. Keep DNS round-robin for macOS. Evaluate a stable regional relay/VIP if
the fleet grows large enough that mobile reprovisioning becomes operationally
expensive.
