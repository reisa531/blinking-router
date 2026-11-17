"""Loopback-only local workbench. Export is the only configuration application path."""
import argparse
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from .config import ROOT, default_config, topology, diff, validate
from .store import Store
from .keepalive import transition
from .status import collect


class Server(ThreadingHTTPServer):
    def __init__(self,address,store,proxy_port=None,manifest_dir="/etc/blinking-router",status_dir="/var/lib"):
        super().__init__(address,Handler)
        self.manifest_dir=manifest_dir; self.status_dir=status_dir
        self.store=store; self.csrf=secrets.token_urlsafe(32); self.proxy_port=proxy_port


class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args): pass
    def send(self,status,body,kind='application/json; charset=utf-8',download=False):
        if not isinstance(body,bytes): body=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if download: self.send_header('Content-Disposition','attachment; filename="blinking-config.tar.gz"')
        self.end_headers(); self.wfile.write(body)
    def allowed(self,write=False):
        host=self.headers.get('Host','')
        allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if self.server.proxy_port:
            allowed |= {f'127.0.0.1:{self.server.proxy_port}',f'localhost:{self.server.proxy_port}'}
        if host not in allowed:
            self.send(403,{'error':'只允许本机访问'}); return False
        if write and (self.headers.get('X-CSRF-Token') != self.server.csrf or self.headers.get('Origin') not in {None,'http://'+host}):
            self.send(403,{'error':'请求来源无效，请刷新页面'}); return False
        return True
    def do_GET(self):
        if not self.allowed(): return
        path=urlparse(self.path).path
        if path=='/api/state':
            c=self.server.store.read()
            self.send(200,{'mode':'local','config':c,'topology':topology(c),'csrf':self.server.csrf,
                'credentials':self.server.store.credentials_status(),'baseline':default_config(),'changes':diff(default_config(),c),
                'services':[{'name':'blinking-panel','state':'running','source':'local'},
                            {'name':'campus-auth','state':'not-deployed','source':'generated'},
                            {'name':'systemd-networkd','state':'not-collected','source':'server'},
                            {'name':'nftables','state':'not-collected','source':'server'}]})
        elif path=='/api/status': self.send(200,collect(self.server.manifest_dir,self.server.status_dir))
        elif path=='/api/export': self.send(200,self.server.store.export(),'application/gzip',True)
        elif path in ('/','/app.js','/extensions.js','/style.css'):
            name='index.html' if path=='/' else path[1:]
            kinds={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','extensions.js':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}
            self.send(200,(ROOT/'blinking/web'/name).read_bytes(),kinds[name])
        else: self.send(404,{'error':'未找到'})
    def do_POST(self):
        if not self.allowed(True): return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 131072: raise ValueError('请求大小无效')
            if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise ValueError('请使用 JSON 请求')
            data=json.loads(self.rfile.read(size))
            if self.path=='/api/config': result={'config':self.server.store.save(data)}
            elif self.path=='/api/validate':
                c=validate(data); result={'valid':True,'topology':topology(c),'changes':diff(self.server.store.read(),c)}
            elif self.path=='/api/credential':
                self.server.store.credential(data['account'],data['password']); result={'saved':True}
            elif self.path=='/api/credential-delete':
                self.server.store.delete_credential(data['account']);result={'deleted':True}
            elif self.path=='/api/simulate':
                event=data['event']; k=self.server.store.read()['keepalive']
                result={'simulated':True,'result':transition(data.get('previous',{}),event,int(time.time()),k['interval'],k['retryMax'],k['failureThreshold'])}
            else: self.send(404,{'error':'未找到'}); return
            self.send(200,result)
        except (ValueError,KeyError,TypeError,AttributeError) as e:
            self.send(400,{'error':str(e) if isinstance(e,ValueError) else '请求结构无效'})


def main():
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=8787); p.add_argument('--state-dir',default='.state')
    p.add_argument('--proxy-port',type=int)
    p.add_argument('--manifest-dir',default='/etc/blinking-router');p.add_argument('--status-dir',default='/var/lib')
    args=p.parse_args(); server=Server(('127.0.0.1',args.port),Store(args.state_dir),args.proxy_port,args.manifest_dir,args.status_dir)
    print(f'Blinking local workbench: http://127.0.0.1:{server.server_port}',flush=True)
    server.serve_forever()

if __name__=='__main__': main()
