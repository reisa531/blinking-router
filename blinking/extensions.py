"""Configuration boundaries for DNS, tunnels and LAN routes."""
import copy
import ipaddress
import re

DEFAULTS = {'ddns': [], 'tunnels': [], 'dns': {'enabled': False, 'upstreams': ['1.1.1.1', '223.5.5.5'], 'cacheSize': 1000}, 'routes': []}


def migrate(c):
    for key, value in DEFAULTS.items():
        c.setdefault(key, copy.deepcopy(value))
    return c


def validate_extensions(c):
    def need(ok, message):
        if not ok: raise ValueError(message)
    def number(n, lo, hi):
        need(type(n) is int and lo <= n <= hi, f'数值须在 {lo}–{hi} 之间')
    def match(s, pattern, label):
        need(isinstance(s, str) and re.fullmatch(pattern, s) is not None, label+'格式无效')
    def host(s):
        match(s, r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', '主机名')
    for section in ['ddns', 'tunnels', 'routes']:
        need(isinstance(c[section], list) and len(c[section]) <= 32, section+' 最多 32 条')
        seen = set()
        for item in c[section]:
            match(item['id'], r'[a-z][a-z0-9-]{0,31}', '规则 ID')
            need(item['id'] not in seen, '规则 ID 重复'); seen.add(item['id'])
            need(type(item['enabled']) is bool, '启用开关无效')
    for d in c['ddns']:
        need(set(d) == {'id','enabled','provider','name','zoneId','recordId','teamId','type','ttl','interval','source','address','proxied'}, 'DDNS 字段无效；Token 请单独保存')
        need(d['provider'] in ('cloudflare','vercel'), '不支持的 DNS 提供商')
        host(d['name'])
        for key in ['zoneId','recordId','teamId']:
            match(d[key], r'[A-Za-z0-9_-]{0,128}', key)
        need(bool(d['recordId']), '请填写已有 DNS 记录的 ID')
        need(d['provider'] != 'cloudflare' or bool(d['zoneId']), 'Cloudflare 需要 Zone ID')
        need(d['type'] in ('A','AAAA'), 'DDNS 支持 A / AAAA')
        number(d['ttl'], 60, 86400); number(d['interval'],60,86400)
        need(type(d['proxied']) is bool, '代理开关无效')
        need(d['provider']=='cloudflare' or not d['proxied'], 'Vercel 不支持 Cloudflare 代理')
        need(d['source'] in ('public','static'), '地址来源无效')
        need(isinstance(d['address'],str), '地址无效')
        if d['source']=='static':
            ip=ipaddress.ip_address(d['address'])
            need(ip.version == (4 if d['type']=='A' else 6) and ip.is_global, 'DDNS 地址必须是对应类型的公网 IP')
    bindings=set()
    for t in c['tunnels']:
        need(set(t)=={'id','enabled','server','serverPort','protocol','localAddress','localPort','remotePort'}, '穿透字段无效；Token 请单独保存')
        host(t['server']); ipaddress.ip_address(t['localAddress'])
        need(t['protocol'] in ('tcp','udp'), '穿透支持 TCP / UDP')
        for k in ['serverPort','localPort','remotePort']: number(t[k],1,65535)
        binding=(t['server'].lower(),t['serverPort'],t['protocol'],t['remotePort'])
        need(not t['enabled'] or binding not in bindings,'穿透远端端口重复')
        if t['enabled']: bindings.add(binding)
    dns=c['dns']
    need(set(dns)=={'enabled','upstreams','cacheSize'}, 'DNS 字段无效')
    need(type(dns['enabled']) is bool,'DNS 开关无效'); number(dns['cacheSize'],0,100000)
    need(isinstance(dns['upstreams'],list) and 1 <= len(dns['upstreams']) <= 4,'请填写 1–4 个上游 DNS')
    lan=ipaddress.IPv4Interface(c['network']['address'])
    for s in dns['upstreams']:
        ip=ipaddress.ip_address(s)
        need(not ip.is_loopback and ip != lan.ip,'DNS 上游不能指向自身')
    destinations=set()
    for r in c['routes']:
        need(set(r)=={'id','enabled','destination','gateway','metric'},'静态路由字段无效')
        dest=ipaddress.IPv4Network(r['destination']); gateway=ipaddress.IPv4Address(r['gateway'])
        need(dest.prefixlen > 0 and not dest.overlaps(lan.network),'目标不能是默认路由或与 LAN 重叠')
        need(gateway in lan.network and gateway not in (lan.ip,lan.network.network_address,lan.network.broadcast_address),'网关须为 LAN 内其他设备')
        number(r['metric'],1,65535)
        need(not r['enabled'] or str(dest) not in destinations,'静态路由目标重复')
        if r['enabled']: destinations.add(str(dest))


def secret_refs(c):
    return {a['id'] for a in c['accounts']} | {f'{kind}:{x["id"]}' for kind in ('ddns','tunnels') for x in c[kind]}
