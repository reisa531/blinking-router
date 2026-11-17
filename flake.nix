{
  description = "Blinking local router workbench and declarative multi-WAN module";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.net-loginer = { url = "github:ShanghaitechGeekPie/net-loginer/3c02725de5964c41870335b5507356b75ee3a0b8"; flake = false; };
  outputs = { self, nixpkgs, net-loginer }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      python = pkgs.python3.withPackages (p: [ p.cryptography ]);
      panel = import ./nix/package.nix { inherit pkgs; };
      loginer = import ./nix/net-loginer.nix { inherit pkgs; src = net-loginer; };
      caddyConfig = pkgs.writeText "Caddyfile" ''
        {
          admin off
          auto_https off
          persist_config off
        }
        http://localhost:8787, http://127.0.0.1:8787 {
          bind 127.0.0.1
          reverse_proxy 127.0.0.1:8788
        }
      '';
      localApp = pkgs.writeShellApplication {
        name = "blinking-local";
        runtimeInputs = [ panel pkgs.caddy ];
        text = ''
          runtime=$(mktemp -d -t blinking-caddy.XXXXXX)
          export XDG_CONFIG_HOME="$runtime/config"
          export XDG_DATA_HOME="$runtime/data"
          blinking-panel --port 8788 --proxy-port 8787 "$@" &
          backend=$!
          trap 'kill "$backend" 2>/dev/null || true; rm -rf "$runtime"' EXIT
          echo 'Caddy local panel: http://127.0.0.1:8787'
          caddy run --config ${caddyConfig} --adapter caddyfile
        '';
      };
      eval = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [ self.nixosModules.default ({ ... }: {
          boot.isContainer = true;
          networking.useHostResolvConf = false;
          system.stateVersion = "26.05";
          services.blinkingRouter = {
            enable = true;
            settings = builtins.fromJSON (builtins.readFile ./examples/router.json);
            package = panel;
            authPackage = loginer;
          };
        }) ];
      };
    in {
      packages.${system} = { default = panel; net-loginer = loginer; local = localApp; };
      apps.${system}.default = { type = "app"; program = "${localApp}/bin/blinking-local"; meta.description = "Local Caddy router workbench"; };
      devShells.${system}.default = pkgs.mkShell { packages = [ python pkgs.nodejs pkgs.nftables pkgs.caddy ]; };
      nixosModules.default = import ./nix/module.nix;
      nixosModules.configured = { ... }: {
        imports = [ self.nixosModules.default ];
        services.blinkingRouter = {
          enable = true;
          settings = builtins.fromJSON (builtins.readFile
            (if builtins.pathExists (self + "/router.json") then self + "/router.json" else ./examples/router.json));
          package = panel;
          authPackage = loginer;
        };
      };
      checks.${system} = {
        auth = pkgs.runCommand "blinking-auth-no-address" { nativeBuildInputs = [ python ]; } ''
          mkdir credentials
          printf local-test > credentials/username
          printf synthetic-test-password > credentials/password
          export CREDENTIALS_DIRECTORY="$PWD/credentials"
          export TARGET_INTERFACE=blinking-test-nonexistent
          set +e
          ${loginer}/bin/net-loginer > result.log 2>&1
          code=$?
          set -e
          test "$code" = 1
          grep '"state":"waiting_address"' result.log
          if grep -q synthetic-test-password result.log; then exit 1; fi
          cp result.log $out
        '';
        network = import ./tests/network.nix { inherit pkgs panel loginer; module = self.nixosModules.default; };
        unit = pkgs.runCommand "blinking-tests" { nativeBuildInputs = [ python ]; } ''
          cp -r ${./blinking} blinking
          cp -r ${./examples} examples
          cp -r ${./nix} nix
          cp -r ${./tests} tests
          python -m unittest discover -s tests -v
          touch $out
        '';
        module = pkgs.writeText "blinking-module-evaluation.json" (builtins.toJSON {
          containers = builtins.attrNames eval.config.containers;
          rules = eval.config.networking.nftables.tables.multiwan.content;
          wan0 = eval.config.containers.wan0.macvlans;
          lan = eval.config.systemd.network.networks."10-lan".networkConfig;
          dhcp = eval.config.systemd.network.networks."10-lan".dhcpServerConfig;
          hostRoutes = eval.config.systemd.network.networks."20-wan0".routes;
          containerNetwork = eval.config.containers.wan0.config.systemd.network.networks."10-wan".networkConfig;
          toplevel = builtins.unsafeDiscardStringContext eval.config.system.build.toplevel.drvPath;
        });
      };
    };
}
