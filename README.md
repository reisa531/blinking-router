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

## Scope

This local milestone implements WAN inventory, stable MAC/slot editing, account mapping, encrypted local credentials, keepalive policy, DHCP reservations, configuration review/export, and NixOS generation. DDNS, tunnels, live remote service discovery, production panel authentication, and timed system activation/rollback are subsequent modules. They are not represented as working controls in the UI. Real campus authentication requires a later authorized deployment and credentials.

## Design

Original UI implementation based on the public Aether preview at https://uiverse.io/design/systems/bad-dodo-83-64e996bc: pearl background, translucent surfaces, inset highlights, pill controls, charcoal accent and restrained blue. The gated CSS/design files were not downloaded; this project does not redistribute them.

## Export snapshot

The repository root `router.json` is the exported configuration snapshot. `nixosModules.configured` reads this file. See [validation results](VALIDATION.md) and [desktop preview](docs/validation/desktop.jpg). Local runtime state and credentials are excluded from Git.
