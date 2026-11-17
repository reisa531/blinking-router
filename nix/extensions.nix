{ config, lib, pkgs, ... }:
let
  cfg = config.services.blinkingRouter;
  c = cfg.settings;
  ddns = builtins.filter (x: x.enabled) (c.ddns or []);
  tunnels = builtins.filter (x: x.enabled) (c.tunnels or []);
  dns = c.dns or { enabled = false; upstreams = []; cacheSize = 1000; };
  routes = builtins.filter (x: x.enabled) (c.routes or []);
  lanIP = builtins.head (lib.splitString "/" c.network.address);
  hardening = { NoNewPrivileges = true; ProtectSystem = "strict"; ProtectHome = true; PrivateTmp = true; };
  ddnsName = d: "blinking-ddns-${d.id}";
  tunnelName = t: "blinking-tunnel-${t.id}";
  frpConfig = t: pkgs.writeText "${tunnelName t}.json" (builtins.toJSON {
    serverAddr = t.server; serverPort = t.serverPort;
    auth = { method = "token"; tokenSource = { type = "file"; file.path = "/run/credentials/${tunnelName t}.service/token"; }; };
    transport.tls.enable = true;
    loginFailExit = false;
    proxies = [{ name = t.id; type = t.protocol; localIP = t.localAddress; localPort = t.localPort; remotePort = t.remotePort; }];
  });
in {
  config = lib.mkIf cfg.enable {
    users.groups.blinking-ddns = lib.mkIf (ddns != []) {};
    users.users.blinking-ddns = lib.mkIf (ddns != []) { isSystemUser = true; group = "blinking-ddns"; };
    systemd.services = builtins.listToAttrs (
      (map (d: lib.nameValuePair (ddnsName d) {
        description = "Blinking DDNS ${d.id}";
        wants = [ "network-online.target" ]; after = [ "network-online.target" ];
        serviceConfig = hardening // {
          Type = "oneshot"; User = "blinking-ddns"; Group = "blinking-ddns";
          LoadCredential = [ "token:${cfg.credentialDirectory}/ddns/${d.id}" ];
          StateDirectory = ddnsName d; StateDirectoryMode = "0755";
          ExecStart = "${cfg.package}/bin/blinking-ddns --config ${pkgs.writeText "ddns-${d.id}.json" (builtins.toJSON d)} --status /var/lib/${ddnsName d}/status.json";
          TimeoutStartSec = 45;
        };
      }) ddns) ++ (map (t: lib.nameValuePair (tunnelName t) {
        description = "Blinking FRP ${t.id}";
        wantedBy = [ "multi-user.target" ]; wants = [ "network-online.target" ]; after = [ "network-online.target" ];
        serviceConfig = hardening // {
          DynamicUser = true;
          LoadCredential = [ "token:${cfg.credentialDirectory}/tunnels/${t.id}" ];
          ExecStart = "${pkgs.frp}/bin/frpc -c ${frpConfig t}";
          Restart = "on-failure"; RestartSec = 10;
        };
      }) tunnels));
    systemd.timers = builtins.listToAttrs (map (d: lib.nameValuePair (ddnsName d) {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnBootSec = "60s"; OnUnitInactiveSec = "${toString d.interval}s"; RandomizedDelaySec = "10s"; };
    }) ddns);
    services.dnsmasq = lib.mkIf dns.enabled {
      enable = true;
      settings = {
        port = 53; interface = c.network.lan; listen-address = lanIP; bind-dynamic = true;
        no-resolv = true; server = dns.upstreams; cache-size = dns.cacheSize;
        domain-needed = true; bogus-priv = true;
      };
    };
    networking.firewall.interfaces.${c.network.lan} = {
      allowedTCPPorts = lib.optional dns.enabled 53;
      allowedUDPPorts = lib.optional dns.enabled 53;
    };
    systemd.network.networks."10-lan".routes = map (r: { Destination = r.destination; Gateway = r.gateway; Metric = r.metric; }) routes;
    networking.firewall.extraForwardRules = lib.mkAfter (lib.concatMapStringsSep "\n" (r:
      ''iifname "${c.network.lan}" oifname "${c.network.lan}" ip daddr ${r.destination} accept'') routes);
    environment.etc."blinking-router/extensions.json".text = builtins.toJSON {
      services = (map (d: { unit = "${ddnsName d}.service"; kind = "ddns"; inherit (d) id interval; }) ddns)
        ++ (map (d: { unit = "${ddnsName d}.timer"; kind = "timer"; }) ddns)
        ++ (map (t: { unit = "${tunnelName t}.service"; kind = "tunnel"; inherit (t) id; }) tunnels)
        ++ lib.optional dns.enabled { unit = "dnsmasq.service"; kind = "dns"; };
    };
  };
}
