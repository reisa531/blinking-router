# Local validation — 2026-11-17

Version 0.2 adds Cloudflare/Vercel DDNS, FRP TCP/UDP tunnels, declared local service/socket status, DNS forwarding/cache, and LAN static routes. The original two-WAN container/macvlan topology remains in the exported snapshot. Development made no SSH connection or network change to Blinking.

## Checks

- `nix flake check path:. --print-build-logs` passed: Python unit/API checks, NixOS module evaluation, the patched authentication binary smoke check, and a QEMU/KVM network test.
- 35 Python tests passed. Added coverage includes old-config migration, provider/address/route validation, duplicate FRP bindings, DNS recursion rejection, service-token save/clear/delete, secret exclusion from archives/API, Cloudflare/Vercel request payloads, IPv4/IPv6 handling, public-IP detection, HTTP errors, redirect rejection, sanitized status files, manifest unit selection, unreadable data and stale DDNS state.
- A local HTTP fixture verified actual PATCH headers and JSON serialization. No real Cloudflare/Vercel account was contacted.
- Nix module evaluation includes DDNS service/timer generation, runtime token loading and the extension manifest.
- Both JavaScript files passed `node --check`.

## Isolated network test

The VM creates a fake campus namespace, two real nspawn containers with macvlan interfaces, and a LAN client namespace. It verifies:

1. DHCP addresses for both WANs, a LAN lease and the LAN default gateway.
2. Stable route marks/tables and successful traffic through both WAN NAT paths.
3. DNS forwarding to a local mock upstream and a cached reply after that upstream stops.
4. A static route through a LAN next hop, actual ICMP traffic to its destination, and the nftables hash bypass rule.
5. FRP TCP traffic to a local HTTP server and FRP UDP traffic to a DNS listener.
6. TCP tunnel reconnection after the local FRP server stops and starts again.
7. Generated service manifests and the installed panel CLI.

The fake upstream DNS process uses the explicit Nix package binary so it does not inherit the host's service wrapper configuration. Test credentials are synthetic. All network interfaces and tunnel peers stay inside the disposable VM.

## Browser validation

In Edge, added and saved a disabled DDNS record, FRP mapping and LAN route. A page reload preserved their values. The review page showed all three sections and configuration validation succeeded. The service page read actual local listening sockets and showed the missing-manifest empty state. A 390 × 844 viewport had no page-width overflow. Screenshots use synthetic `example.com` records and contain no tokens. Test entries were deleted after validation.

The preview runs locally at `http://127.0.0.1:8789`; the standard packaged Caddy entry point remains `nix run path:.` on port 8787. The transient local development service is `blinking-dev-v02.service` in the user systemd instance and can be stopped with `systemctl --user stop blinking-dev-v02`.

## Evidence

- [Nix build and VM log](docs/validation/v0.2/nix-check.log)
- [Unit/API log](docs/validation/v0.2/unit-tests.log)
- [Desktop DDNS view](docs/validation/v0.2/ddns-desktop.jpg)
- [Mobile DDNS view](docs/validation/v0.2/ddns-mobile.jpg)

Earlier v0.1 validation artifacts remain in `docs/validation/`. Current capability and deployment boundaries are listed once in the README's Scope section. These results establish local functionality, not live provider authorization or campus login.
