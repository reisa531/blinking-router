"""One worker per WAN namespace. No network changes are performed here."""
import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from .store import atomic

STATES = {'online','portal','network_error','invalid_password','locked','captcha_error','login_error','waiting_address'}


def transition(previous, event, now, interval=60, retry_max=900, threshold=3):
    if event not in STATES: raise ValueError('Unknown health event')
    failures = 0 if event == 'online' else previous.get('failures',0)+1
    paused = event in ('invalid_password','locked')
    delay = min(retry_max,interval * 2**min(max(failures-1,0),10))
    return {'state':event,'failures':failures,'healthy':event == 'online',
            'eligible':event == 'online' or (previous.get('eligible',False) and failures < threshold and not paused),
            'paused':paused,'checkedAt':now,'lastOnline':now if event=='online' else previous.get('lastOnline'),
            'nextCheck':None if paused else now+(interval if event=='online' else delay)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None


def probe(timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open('http://connectivitycheck.gstatic.com/generate_204',timeout=timeout) as r:
            code = r.status
            body = r.read(8192).decode('utf-8',errors='replace')
        if code == 204:
            with opener.open('https://connectivitycheck.gstatic.com/generate_204',timeout=timeout) as r:
                return 'online' if r.status == 204 else 'network_error'
        return 'portal' if 'net-auth.shanghaitech.edu.cn' in body else 'network_error'
    except urllib.error.HTTPError as e:
        location = e.headers.get('Location','')
        from urllib.parse import urlparse
        return 'portal' if urlparse(location).hostname == 'net-auth.shanghaitech.edu.cn' else 'network_error'
    except (urllib.error.URLError,TimeoutError,OSError):
        return 'network_error'


def login(binary,timeout):
    # The patched upstream reports one machine-readable result and never prints credentials.
    try:
        r = subprocess.run([binary],capture_output=True,text=True,timeout=timeout,check=False)
    except subprocess.TimeoutExpired:
        return 'login_error'
    try:
        result = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        return 'login_error'
    state = result.get('state')
    if state == 'success' and r.returncode == 0: return 'success'
    return state if state in STATES else 'login_error'


def check_and_authenticate(timeout, binary, probe_fn=probe, login_fn=login):
    event = probe_fn(timeout)
    if event == 'portal':
        event = login_fn(binary, 90)
        if event == 'success':
            event = probe_fn(timeout)
    return event


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--interval',type=int,default=60); p.add_argument('--timeout',type=int,default=15)
    p.add_argument('--retry-max',type=int,default=900); p.add_argument('--threshold',type=int,default=3)
    p.add_argument('--binary',required=True); p.add_argument('--state-file',required=True)
    args=p.parse_args()
    state={}
    while True:
        event=check_and_authenticate(args.timeout,args.binary)
        state=transition(state,event,int(time.time()),args.interval,args.retry_max,args.threshold)
        atomic(Path(args.state_file),json.dumps(state).encode(),0o644)
        if state['paused']:
            # Restart after correcting credentials; avoid repeated account lockouts.
            return
        time.sleep(max(1,state['nextCheck']-int(time.time())))


if __name__ == '__main__': main()
