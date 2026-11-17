{ pkgs, module, panel, loginer }:
let
  original = builtins.fromJSON (builtins.readFile ../examples/router.json);
  settings = original // {
    network = original.network // { parent = "uplink"; lan = "lan0"; };
    dns = { enabled = true; upstreams = [ "192.168.64.2" ]; cacheSize = 100; };
    routes = [{ id = "downstream"; enabled = true; destination = "10.30.0.0/16"; gateway = "192.168.64.2"; metric = 100; }];
    tunnels = [{ id = "test"; enabled = true; server = "127.0.0.1"; serverPort = 7000; protocol = "tcp"; localAddress = "127.0.0.1"; localPort = 8889; remotePort = 6000; }
      { id = "test-udp"; enabled = true; server = "127.0.0.1"; serverPort = 7000; protocol = "udp"; localAddress = "192.168.64.1"; localPort = 53; remotePort = 6053; }];
  };
in pkgs.testers.runNixOSTest {
  name = "blinking-macvlan-routing";
  nodes.router = { lib, ... }: {
    imports = [ module ];
    virtualisation.memorySize = 1536;
    services.blinkingRouter = { enable = true; inherit settings; package = panel; authPackage = loginer; };
    containers.wan0.autoStart = lib.mkForce false;
    containers.wan1.autoStart = lib.mkForce false;
    environment.systemPackages = [ pkgs.iproute2 pkgs.dnsmasq pkgs.dhcpcd pkgs.nftables pkgs.tcpdump pkgs.frp pkgs.curl pkgs.python3 pkgs.dig ];
  };
  testScript = ''
    start_all()
    router.wait_for_unit("multi-user.target")
    # All interfaces are created inside this disposable VM; no host/campus access.
    router.succeed("ip netns add campus")
    router.succeed("ip link add uplink type veth peer name upstream")
    router.succeed("ip link set upstream netns campus")
    router.succeed("ip link set uplink up")
    router.succeed("ip netns exec campus ip link set lo up")
    router.succeed("ip netns exec campus ip link set upstream up")
    router.succeed("ip netns exec campus ip addr add 10.20.0.1/24 dev upstream")
    router.succeed("ip netns exec campus ip addr add 198.18.0.1/32 dev lo")
    router.succeed("systemd-run --unit=mock-campus ip netns exec campus ${pkgs.dnsmasq}/bin/dnsmasq --user=root --dhcp-leasefile=/tmp/mock-campus.leases --keep-in-foreground --port=0 --interface=upstream --bind-interfaces --dhcp-range=10.20.0.10,10.20.0.20,255.255.255.0,1h --dhcp-option=3,10.20.0.1 --dhcp-host=66:6b:70:ed:f9:b0,10.20.0.10 --dhcp-host=66:6b:70:09:4b:80,10.20.0.11")
    router.wait_for_unit("mock-campus.service")
    router.succeed("ip netns add client")
    router.succeed("ip link add lan0 type veth peer name client0")
    router.succeed("ip link set client0 netns client")
    router.succeed("ip link set lan0 up")
    router.succeed("ip netns exec client ip link set lo up")
    router.succeed("ip netns exec client ip link set client0 up")
    router.succeed("networkctl reload")
    router.wait_until_succeeds("ip -4 addr show lan0 | grep 192.168.64.1")
    router.succeed("systemctl start container@wan0 container@wan1")
    for wan, address in (("wan0", "10.20.0.10"), ("wan1", "10.20.0.11")):
        leader = router.succeed(f"machinectl show {wan} -p Leader --value").strip()
        router.wait_until_succeeds(f"nsenter -t {leader} -n ip -4 addr show wan | grep {address}", timeout=45)
    router.succeed("ip netns exec client dhcpcd -4 -1 -C resolv.conf -t 30 client0")
    router.succeed("ip netns exec client ip -4 addr show client0 | grep 192.168.64.")
    router.succeed("ip netns exec client ip route | grep 'default via 192.168.64.1'")
    router.succeed("ip route get 198.18.0.1 from 192.168.64.20 iif lan0 mark 1 | grep 'via 192.168.251.1'")
    router.succeed("ip route get 198.18.0.1 from 192.168.64.20 iif lan0 mark 2 | grep 'via 192.168.251.3'")
    router.succeed("nft list table inet multiwan | grep jhash")
    router.succeed("ip netns exec client ping -c 3 -W 3 198.18.0.1")
    # Force each mark in the disposable VM to exercise both NAT paths independently.
    for mark in (1, 2):
        router.succeed(f"nft insert rule inet multiwan prerouting iifname \\\"lan0\\\" ct mark set {mark} meta mark set {mark} return")
        router.succeed("ip netns exec client ping -c 2 -W 3 198.18.0.1")
    router.succeed("test -f /etc/blinking-router/manifest.json")
    router.wait_for_unit("dnsmasq.service")
    router.succeed("ip netns exec client ip addr add 192.168.64.2/24 dev client0")
    router.succeed("ip netns exec client ip addr add 10.30.0.5/32 dev lo")
    router.succeed("ping -c 2 -W 3 10.30.0.5")
    router.succeed("systemd-run --unit=mock-dns ip netns exec client ${pkgs.dnsmasq}/bin/dnsmasq --log-facility=- --conf-file=/dev/null --pid-file=/tmp/mock-dns.pid --user=root --keep-in-foreground --no-resolv --no-hosts --interface=client0 --bind-interfaces --local-ttl=300 --address=/router.test/1.1.1.1")
    router.wait_for_unit("mock-dns.service")
    router.wait_until_succeeds("ip netns exec client dig @192.168.64.1 router.test +short +time=1 +tries=1 | grep 1.1.1.1")
    router.succeed("systemctl stop mock-dns")
    router.succeed("ip netns exec client dig @192.168.64.1 router.test +short +time=1 +tries=1 | grep 1.1.1.1")
    router.succeed("ip netns exec client dig @192.168.64.1 localhost +short | grep 127.0.0.1")
    router.succeed("ip route get 10.30.0.5 | grep 'via 192.168.64.2'")
    router.succeed("nft list table inet multiwan | grep 10.30.0.0/16")
    router.succeed("mkdir -p /run/blinking-credentials/tunnels; printf local-test-token > /run/blinking-credentials/tunnels/test; chmod 600 /run/blinking-credentials/tunnels/test")
    router.succeed("printf 'bindPort = 7000\\nauth.token = \"local-test-token\"\\n' > /tmp/frps.toml")
    router.succeed("systemd-run --unit=frps-test frps -c /tmp/frps.toml")
    router.succeed("systemd-run --unit=http-test python -m http.server 8889 --bind 127.0.0.1 --directory /etc/blinking-router")
    router.succeed("systemctl restart blinking-tunnel-test.service")
    router.wait_until_succeeds("curl -fsS http://127.0.0.1:6000/manifest.json | grep revision")
    router.succeed("cp /run/blinking-credentials/tunnels/test /run/blinking-credentials/tunnels/test-udp")
    router.succeed("systemctl restart blinking-tunnel-test-udp.service")
    router.wait_until_succeeds("dig @127.0.0.1 -p 6053 localhost +short +time=1 +tries=1 | grep 127.0.0.1")
    router.succeed("systemctl stop frps-test")
    router.succeed("systemd-run --unit=frps-test frps -c /tmp/frps.toml")
    router.wait_until_succeeds("curl -fsS http://127.0.0.1:6000/manifest.json | grep revision", timeout=60)
    router.succeed("${panel}/bin/blinking-panel --help | grep manifest-dir")
  '';
}
