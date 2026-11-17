# Local validation — 2026-11-17

The local milestone provides WAN count/enablement, fixed MAC and slot editing, campus account mapping, encrypted local credentials, keepalive policy and simulation, DHCP reservations, configuration review, and a Nix export. No SSH command or network mutation was performed on Blinking during implementation.

## Passed checks

- `nix flake check path:.`: all checks passed. Checks cover the Python unit/API suite, NixOS module evaluation, a patched authentication binary smoke test, and the isolated network VM.
- 22 Python tests passed: stable addressing, MAC validation, account references, DHCP validation, stale-write rejection, encrypted credential persistence, export exclusion, local API round trip, CSRF/Host checks, health backoff, recovery, and credential-error pauses.
- `nix build path:.#net-loginer`: built the pinned Rust client with ONNX Runtime and credential/logging patches.
- The actual patched binary loaded its model and returned `waiting_address` for a nonexistent test interface. A synthetic password was absent from stdout/stderr; no portal request was made.
- A NixOS QEMU/KVM test created a fake campus namespace, two real nspawn containers with macvlan interfaces, and a LAN client namespace. WANs obtained `10.20.0.10` and `10.20.0.11` from the fake upstream DHCP server. The LAN client obtained a `192.168.64.x` lease and gateway `192.168.64.1`. Routing marks 1 and 2 selected the expected /31 next hops. Both NAT paths reached the simulated external address `198.18.0.1`.
- A review archive exported through Caddy was extracted and evaluated as a flake. Its `nixosModules.configured` read the edited root `router.json`; an enabled authentication account produced the expected worker unit, credential slots and `veth-wan0` declaration.
- Browser checks passed: add/save/reload/remove a third WAN, add a synthetic account, map it to WAN0, enable keepalive, save, and remove the synthetic records. Password-error replay paused retries. Configuration validation succeeded. Desktop and 390px layouts were inspected; the final browser error log was empty.

## Artifacts

Evidence is in `docs/validation/`: desktop/mobile screenshots and build/test logs. UI test data was removed; the local workspace retains the original two WANs, no account credentials, and revision 6.

## Deployment boundary

The app binds to loopback through Caddy over HTTP for local development. It exports configuration but has no activation endpoint. Production login/TLS, live server service/port collection, dynamic health-based route removal, DDNS, tunnels, and timed deployment rollback remain separate milestones. Runtime credentials must be provisioned outside the Nix store before enabling authentication on a real host. Actual campus login and school session limits have not been tested.
