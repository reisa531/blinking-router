"""Validated configuration shared by the API, exporter and Nix module."""
import copy
import ipaddress
import json
import re
from pathlib import Path
from .extensions import migrate, validate_extensions

ROOT = Path(__file__).resolve().parent.parent


def default_config():
    return migrate(json.loads((ROOT / 'examples/router.json').read_text()))


def validate(data):
    c = copy.deepcopy(data)
    def require(ok, message):
        if not ok:
            raise ValueError(message)
    def integer(value, lo, hi, label):
        require(type(value) is int and lo <= value <= hi, f'{label}必须在 {lo}–{hi} 之间')
    def text(value, pattern, label):
        require(isinstance(value, str) and re.fullmatch(pattern, value) is not None, f'{label}格式无效')
    def mac(value):
        text(value, r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', 'MAC')
        require(not int(value[:2], 16) & 1 and value.lower() != '00:00:00:00:00:00', 'MAC 必须是有效单播地址')
        return value.lower()
    require(isinstance(c, dict), '配置必须是对象')
    migrate(c)
    require(set(c) == {'version','revision','network','keepalive','accounts','wans','dhcp','ddns','tunnels','dns','routes'}, '配置字段不完整或包含未知字段')
    require(c['version'] == 1, '不支持的配置版本')
    integer(c['revision'], 0, 10**9, '配置版本')
    n = c['network']
    require(set(n) == {'parent','lan','address','transit'}, '网络字段无效')
    for field in ['parent', 'lan']:
        text(n[field], r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}', '接口名')
    require(n['parent'] != n['lan'], '上联与 LAN 不能使用同一接口')
    address = ipaddress.IPv4Interface(n['address'])
    require(address.network.prefixlen == 24, '本版 LAN 使用 /24 网段')
    require(address.ip not in (address.network.network_address, address.network.broadcast_address), 'LAN 地址不能是网络或广播地址')
    transit = ipaddress.IPv4Network(n['transit'])
    require(transit.prefixlen == 24 and not transit.overlaps(address.network), '中转网段应为与 LAN 不重叠的 /24')
    k = c['keepalive']
    require(set(k) == {'interval','timeout','retryMax','failureThreshold'}, '保活字段无效')
    for field, lo, hi in [('interval',15,3600),('timeout',3,60),('retryMax',60,86400),('failureThreshold',1,10)]:
        integer(k[field],lo,hi,field)
    require(k['retryMax'] >= k['interval'], '最大重试间隔不能小于检查间隔')
    require(isinstance(c['accounts'],list) and len(c['accounts']) <= 32, '账号最多 32 个')
    account_ids = set()
    for a in c['accounts']:
        require(set(a) == {'id','name','username'}, '账号只接受 ID、名称和用户名，密码请单独保存')
        text(a['id'],r'[a-z][a-z0-9-]{0,31}','账号 ID')
        require(a['id'] not in account_ids, '账号 ID 重复')
        account_ids.add(a['id'])
        for key in ['name','username']:
            require(isinstance(a[key],str) and 1 <= len(a[key]) <= 128 and not any(ord(x)<32 for x in a[key]), '账号名称或用户名无效')
    require(isinstance(c['wans'], list) and 1 <= len(c['wans']) <= 16, 'WAN 数量必须为 1–16')
    ids, macs, slots = set(), set(), set()
    for w in c['wans']:
        require(set(w) == {'id','slot','name','mac','enabled','account','keepalive'}, 'WAN 字段无效')
        integer(w['slot'],0,15,'WAN 槽位')
        require(w['id'] == f"wan{w['slot']}", 'WAN ID 必须与固定槽位一致')
        require(w['id'] not in ids and w['slot'] not in slots, 'WAN ID 或槽位重复')
        ids.add(w['id']); slots.add(w['slot'])
        w['mac'] = mac(w['mac'])
        require(w['mac'] not in macs, 'WAN MAC 地址重复')
        macs.add(w['mac'])
        require(isinstance(w['name'],str) and 1 <= len(w['name']) <= 64,'WAN 名称须为 1–64 个字符')
        require(type(w['enabled']) is bool and type(w['keepalive']) is bool, '启停字段必须为布尔值')
        require(w['account'] == '' or w['account'] in account_ids, 'WAN 引用了不存在的账号')
        require(not w['keepalive'] or bool(w['account']), '启用保活前请选择认证账号')
    require(any(w['enabled'] for w in c['wans']), '至少保留一个启用的 WAN')
    d = c['dhcp']
    require(set(d) == {'enabled','start','size','leaseSeconds','dns','reservations'}, 'DHCP 字段无效')
    require(type(d['enabled']) is bool,'DHCP 开关无效')
    integer(d['start'],1,254,'地址池起点'); integer(d['size'],1,253,'地址池大小')
    require(d['start']+d['size'] <= 255,'DHCP 地址池超出网段')
    integer(d['leaseSeconds'],60,604800,'租期')
    require(isinstance(d['dns'],list) and 1 <= len(d['dns']) <= 4,'请填写 1–4 个 DNS 地址')
    for dns in d['dns']: ipaddress.IPv4Address(dns)
    require(isinstance(d['reservations'],list) and len(d['reservations']) <= 253,'静态租约无效')
    ips, reserved_macs = set(), set()
    for r in d['reservations']:
        require(set(r) == {'name','mac','address'}, '静态租约字段无效')
        require(isinstance(r['name'],str) and len(r['name']) <= 64,'设备名称过长')
        r['mac'] = mac(r['mac']); ip = ipaddress.IPv4Address(r['address'])
        require(ip in address.network and ip not in (address.ip,address.network.network_address,address.network.broadcast_address),'静态地址不在 LAN 可用范围')
        require(str(ip) not in ips and r['mac'] not in reserved_macs,'静态租约的 IP 或 MAC 重复')
        ips.add(str(ip)); reserved_macs.add(r['mac'])
    validate_extensions(c)
    return c


def topology(c):
    base = int(ipaddress.IPv4Network(c['network']['transit']).network_address)
    return [{**w, 'hostAddress': str(ipaddress.IPv4Address(base+w['slot']*2)),
             'peerAddress': str(ipaddress.IPv4Address(base+w['slot']*2+1)),
             'mark': w['slot']+1, 'table': 101+w['slot']} for w in c['wans']]


def diff(old, new):
    changes = []
    for key in ('network','keepalive','accounts','wans','dhcp','ddns','tunnels','dns','routes'):
        if old[key] != new[key]:
            changes.append({'section': key, 'before': old[key], 'after': new[key]})
    return changes
