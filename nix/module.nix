{ config, lib, pkgs, ... }:
let
  cfg = config.services.blinkingRouter;
  c = cfg.settings;
  active = builtins.filter (w: w.enabled) c.wans;
  base = lib.concatStringsSep "." (lib.take 3 (lib.splitString "." c.network.transit));
  host = w: "${base}.${toString (w.slot * 2)}";
  peer = w: "${base}.${toString (w.slot * 2 + 1)}";
  mark = w: w.slot + 1;
  table = w: 101 + w.slot;
  lanNet = "${lib.concatStringsSep "." (lib.take 3 (lib.splitString "." c.network.address))}.0/24";
  account = w: lib.findFirst (a: a.id == w.account) null c.accounts;
  auth = w: w.keepalive && w.account != "";
  mapping = lib.imap0 (index: w: "${toString index} : ${toString (mark w)}") active;
  vethNetworks = builtins.listToAttrs (map (w: lib.nameValuePair "20-${w.id}" {
    matchConfig.Name = "veth-${w.id}";
    linkConfig.RequiredForOnline = false;
    address = [ "${host w}/31" ];
    routes = [ { Destination = "0.0.0.0/0"; Gateway = peer w; Table = table w; }
               { Destination = "0.0.0.0/0"; Type = "unreachable"; Metric = 65535; Table = table w; } ];
  }) active);
in {
  options.services.blinkingRouter = {
    enable = lib.mkEnableOption "declarative Blinking multi-WAN networking";
    settings = lib.mkOption { type = lib.types.attrs; description = "Validated router.json configuration."; };
    package = lib.mkOption { type = lib.types.package; default = import ./package.nix { inherit pkgs; }; };
    authPackage = lib.mkOption { type = lib.types.package; description = "Patched net-loginer package."; };
    credentialDirectory = lib.mkOption { type = lib.types.str; default = "/run/blinking-credentials"; description = "Root-owned runtime files, provisioned separately from Nix store."; };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      { assertion = active != []; message = "Blinking requires at least one enabled WAN."; }
      { assertion = lib.length c.wans <= 16; message = "Blinking supports up to 16 WAN slots."; }
      { assertion = lib.length (lib.unique (map (w: w.slot) c.wans)) == lib.length c.wans; message = "WAN slots must be unique."; }
      { assertion = lib.length (lib.unique (map (w: lib.toLower w.mac) c.wans)) == lib.length c.wans; message = "WAN MAC addresses must be unique."; }
      { assertion = builtins.all (w: w.id == "wan${toString w.slot}" && w.slot >= 0 && w.slot < 16) c.wans; message = "WAN IDs must match fixed slots 0–15."; }
      { assertion = builtins.all (w: !auth w || account w != null) active; message = "Authenticated WAN requires an account."; }
    ];
    networking.useNetworkd = true;
    networking.nftables.enable = true;
    boot.kernel.sysctl."net.ipv4.ip_forward" = 1;
    systemd.network.networks = vethNetworks // {
      "10-lan" = {
        matchConfig.Name = c.network.lan;
        linkConfig.RequiredForOnline = false;
        address = [ c.network.address ];
        networkConfig.DHCPServer = c.dhcp.enabled;
        dhcpServerConfig = {
          PoolOffset = c.dhcp.start; PoolSize = c.dhcp.size;
          DefaultLeaseTimeSec = c.dhcp.leaseSeconds; MaxLeaseTimeSec = lib.max 43200 c.dhcp.leaseSeconds;
          DNS = c.dhcp.dns;
        };
        dhcpServerStaticLeases = map (r: { MACAddress = r.mac; Address = r.address; }) c.dhcp.reservations;
        routingPolicyRules = map (w: {
          FirewallMark = mark w; IncomingInterface = c.network.lan;
          Priority = 1000; Table = table w;
        }) active;
      };
    };
    networking.nftables.tables.multiwan = {
      family = "inet";
      content = ''
        chain prerouting {
          type filter hook prerouting priority mangle; policy accept;
          iifname "${c.network.lan}" ct state new ct mark set jhash ip saddr . ip daddr mod ${toString (lib.length active)} map { ${lib.concatStringsSep ", " mapping} }
          iifname "${c.network.lan}" meta mark set ct mark
        }
      '';
    };
    networking.firewall = {
      filterForward = true;
      interfaces.${c.network.lan}.allowedUDPPorts = lib.optional c.dhcp.enabled 67;
      extraForwardRules = ''iifname "${c.network.lan}" oifname "veth-wan*" accept'';
    };
    containers = builtins.listToAttrs (map (w: lib.nameValuePair w.id {
      autoStart = true;
      privateNetwork = true;
      hostBridge = null;
      extraVeths."veth-${w.id}" = { };
      macvlans = [ "${c.network.parent}:wan" ];
      bindMounts = lib.optionalAttrs (auth w) {
        "/run/campus-credentials" = { hostPath = "${cfg.credentialDirectory}/${w.account}"; isReadOnly = true; };
      };
      config = { ... }: {
        system.stateVersion = "26.05";
        networking.useNetworkd = true;
        networking.useDHCP = false;
        networking.useHostResolvConf = false;
        networking.nftables.enable = true;
        networking.nat = { enable = true; externalInterface = "wan"; internalInterfaces = [ "veth-${w.id}" ]; };
        networking.firewall.enable = true;
        services.resolved.enable = true;
        systemd.network.networks = {
          "10-wan" = {
            matchConfig.Name = "wan";
            linkConfig.MACAddress = w.mac;
            networkConfig.DHCP = "ipv4";
            dhcpV4Config.ClientIdentifier = "mac";
          };
          "10-veth" = {
            matchConfig.Name = "veth-${w.id}";
            linkConfig.RequiredForOnline = false;
            address = [ "${peer w}/31" ];
            routes = [ { Destination = lanNet; Gateway = host w; } ];
          };
        };
        systemd.services.campus-auth = lib.mkIf (auth w) {
          description = "Campus authentication and health checks";
          wantedBy = [ "multi-user.target" ];
          wants = [ "network-online.target" ]; after = [ "network-online.target" ];
          environment = { TARGET_INTERFACE = "wan"; };
          serviceConfig = {
            DynamicUser = true;
            LoadCredential = [ "username:/run/campus-credentials/username" "password:/run/campus-credentials/password" ];
            StateDirectory = "campus-auth";
            ExecStart = "${cfg.package}/bin/blinking-keepalive --binary ${cfg.authPackage}/bin/net-loginer --interval ${toString c.keepalive.interval} --timeout ${toString c.keepalive.timeout} --retry-max ${toString c.keepalive.retryMax} --threshold ${toString c.keepalive.failureThreshold} --state-file /var/lib/campus-auth/status.json";
            Restart = "on-failure"; RestartSec = 30;
            NoNewPrivileges = true; ProtectSystem = "strict"; ProtectHome = true; PrivateTmp = true;
          };
        };
      };
    }) active);
    environment.etc."blinking-router/manifest.json".text = builtins.toJSON {
      revision = c.revision;
      services = [ "systemd-networkd.service" "nftables.service" ] ++ map (w: "container@${w.id}.service") active;
      containers = map (w: { inherit (w) id slot; authentication = auth w; }) active;
    };
  };
}
