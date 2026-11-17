"""One-shot DDNS updater, scheduled by systemd; no network writes from the panel."""
import argparse
import ipaddress
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from .store import atomic


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, token=None, payload=None):
    headers={'Accept':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    if payload is not None: headers['Content-Type']='application/json'
    req=urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(),
                               headers=headers, method='GET' if payload is None else 'PATCH')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                     urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(req,timeout=15) as response:
        data=json.loads(response.read(65536))
        if not isinstance(data,dict): raise ValueError("invalid-provider-response")
        return data


def update(record, token, transport=request):
    version=4 if record['type']=='A' else 6
    address=record['address'] if record['source']=='static' else transport(
        'https://api.ipify.org?format=json' if version==4 else 'https://api6.ipify.org?format=json')['ip']
    ip=ipaddress.ip_address(address)
    if ip.version!=version or not ip.is_global:
        raise ValueError('address-not-public-or-wrong-family')
    body={'type':record['type'],'ttl':record['ttl']}
    if record['provider']=='cloudflare':
        url=f'https://api.cloudflare.com/client/v4/zones/{record["zoneId"]}/dns_records/{record["recordId"]}'
        body.update(content=str(ip),proxied=record['proxied'])
        result=transport(url,token,body)
        if result.get('success') is not True: raise ValueError('provider-rejected')
    else:
        url=f'https://api.vercel.com/v1/domains/records/{record["recordId"]}'
        if record['teamId']: url+='?'+urllib.parse.urlencode({'teamId':record['teamId']})
        body['value']=str(ip)
        result=transport(url,token,body)
        if result.get('error') or not result.get('id'): raise ValueError('provider-rejected')
    return {'state':'updated','address':str(ip),'checkedAt':int(time.time()),'provider':record['provider']}


def run(record, token, status_path, transport=request):
    try:
        result=update(record,token,transport)
    except urllib.error.HTTPError as error:
        result={'state':'error','reason':f'http-{error.code}'}
    except (urllib.error.URLError, TimeoutError, OSError):
        result={'state':'error','reason':'connection-failed'}
    except (ValueError, KeyError, TypeError):
        result={'state':'error','reason':'invalid-address-or-provider-response'}
    result['checkedAt']=int(time.time())
    # Provider bodies and exception strings can contain secrets and are never logged.
    atomic(status_path,json.dumps(result).encode(),0o644)
    return 0 if result['state']=='updated' else 1


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--status',required=True)
    args=p.parse_args()
    record=json.loads(Path(args.config).read_text())
    token=(Path(os.environ['CREDENTIALS_DIRECTORY'])/'token').read_text().strip()
    raise SystemExit(run(record,token,args.status))

if __name__=='__main__':main()
