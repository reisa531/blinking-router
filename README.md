# Blinking Router Workbench

A local configuration editor for the existing NixOS macvlan/container topology on Blinking. The API binds to loopback and can save drafts, validate configuration, store encrypted credentials, replay health events, and export a review archive. It has no SSH or system activation endpoint.

## Run

```sh
nix run path:. -- --state-dir .state
nix flake check path:.
nix build path:.#net-loginer
```

Open http://127.0.0.1:8787 (Caddy → loopback API on 8788). For development, use `nix develop path:.` and `python -m blinking.server`.

## Configuration

WAN slots remain stable across edits: slot 0 uses mark 1, table 101 and the first /31 transit pair. Each enabled WAN creates an nspawn container and a macvlan named `wan` on the configured parent. LAN traffic uses the existing source/destination IPv4 hash and connection marks. The module preserves the host management interface and unrelated IPMI configuration by not declaring them.

Review exports include `router.json`, the Nix module, packages, source and tests. Add the exported flake as an input to the original host configuration and import its `nixosModules.configured`. That module reads the exported root `router.json` and supplies the panel and authentication packages. Alternatively, import `nix/module.nix` and set its options explicitly. The application does not activate exports. Review and reconcile existing declarations before importing the module; duplicate owners of the LAN or multiwan table must be removed in the deployment branch.

## Credentials

Local passwords are Fernet-encrypted with a generated key in the private state directory; both are mode 0600. This protects exports and accidental config disclosure, not compromise of the same OS user. Neither key nor encrypted vault is included in exports. Runtime provisioning uses root-owned files outside the Nix store at `/run/blinking-credentials/<account-id>/{username,password}`. A separate deployment step must provision them, for example with sops-nix. Container services receive these files via systemd credentials. The workbench never contacts the campus portal.

## Authentication

The upstream source is pinned in `flake.lock`. A narrow patch removes credential logging, reads systemd credentials, selects only `wan`, bounds captcha retries and emits a structured result. Each worker probes from its own container namespace. Only a recognized campus redirect triggers authentication. A timeout or generic connectivity failure does not submit credentials. Invalid credentials or a locked account stop the worker until an administrator corrects the secret and restarts it.

Health replay in the UI uses the actual transition function with synthetic events. It never represents server status. The current module keeps static hash distribution: health status is recorded but does not yet dynamically edit the live nftables map. Existing connections remain tied to their connection marks.

## DDNS

The editor manages existing Cloudflare and Vercel A/AAAA records. A record's `recordId` (and Cloudflare `zoneId`) selects the update target; `name` is a display label. Vercel accepts an optional `teamId`. Each enabled entry creates a one-shot `blinking-ddns-<id>.service` and timer. Every interval it detects the host's public IPv4/IPv6 using ipify, or uses a configured public address, then patches the provider record. Cloudflare supports its proxy flag. Requests use verified HTTPS, a 15-second timeout, no environment proxy and no redirects. Failed attempts record a sanitized reason and retry on the next timer interval.

The worker writes `/var/lib/blinking-ddns-<id>/status.json` with the last attempt time, result and address. Status is stale after twice the configured interval (minimum 180 seconds). It records API acknowledgement, not authoritative DNS propagation. Both DDNS and FRP use the host's existing default route; WAN-specific worker placement is not implemented.

Provision the runtime DDNS token as `/run/blinking-credentials/ddns/<id>` and FRP token as `/run/blinking-credentials/tunnels/<id>` (root-owned, mode 0600), or change `credentialDirectory`. systemd loads each token into the corresponding unit. Local UI tokens use separate encrypted vault entries and are excluded from review exports. Saving a draft never calls the provider API or starts a tunnel.

Provider contracts: [Cloudflare DNS record update](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/edit/), [Vercel DNS record update](https://vercel.com/docs/rest-api/dns/update-an-existing-dns-record).

## Tunnels

Each FRP TCP/UDP mapping declares a server/control port, target IP/port, and remote public port. Nix generates a `blinking-tunnel-<id>.service` with TLS transport, automatic reconnection, restart-on-failure, and a token file supplied through `LoadCredential`. The FRP server must already exist and allow the selected remote ports. The service state is the client process state; it does not assert that a remote user can reach the target. FRP uses its default TLS trust behavior; custom CA/server identity settings are a future extension. See [FRP token files](https://gofrp.org/en/docs/features/common/authentication/).

## DNS and LAN routes

Optional dnsmasq listens on the LAN address, forwards to configured upstream IPs and caches replies. networkd remains the DHCP server. When local DNS is enabled, DHCP advertises the router's LAN IP. Static routes use another LAN device as the gateway for a downstream IPv4 subnet. Their destinations bypass WAN hashing, receive main-table routes, and get explicit LAN-to-LAN forwarding rules. Default routes and overlapping LAN destinations are rejected by the editor.

## Local runtime status

`/api/status` reads only `*.json` in `--manifest-dir` (default `/etc/blinking-router`). Each manifest declares `services` as unit names or objects with `unit`, `kind`, and optional `id`/`interval`. The Nix modules generate `manifest.json` and `extensions.json`. Add another declarative file to this directory to observe an existing host service. For example:

```json
{"services": ["sshd.service", "caddy.service"]}
```

The collector queries these units with `systemctl show`, reads DDNS status only for declared IDs under `--status-dir` (default `/var/lib`), and lists listening TCP/UDP sockets using `ss`. It reports missing units, unavailable queries, unreadable manifests and stale DDNS observations. It does not search the filesystem. Ports belong to the panel process's network namespace; container-internal sockets are not included. Runtime observation is local and has no service restart/stop controls.

## Scope

Version 0.2 includes WAN/MAC/account management, keepalive policy, DHCP reservations, DDNS workers, FRP clients, local service/port observations, DNS forwarding/cache, static LAN routes and Nix review exports. Production panel login/TLS, WAN health-based redistribution, WAN-specific DDNS/tunnels, general firewall/port-forward editing, IPv6 LAN routing, and timed activation/rollback remain separate work. Real provider credentials and campus authentication have not been exercised; validation uses local mock APIs and isolated NixOS virtual machines.

## Design

Original UI implementation based on the public Aether preview at https://uiverse.io/design/systems/bad-dodo-83-64e996bc: pearl background, translucent surfaces, inset highlights, pill controls, charcoal accent and restrained blue. The gated CSS/design files were not downloaded; this project does not redistribute them.

## Export snapshot

The repository root `router.json` is the exported configuration snapshot. `nixosModules.configured` reads this file. See [validation results](VALIDATION.md) and [desktop preview](docs/validation/desktop.jpg). Local runtime state and credentials are excluded from Git.
