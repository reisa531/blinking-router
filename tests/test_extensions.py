import io
import json
import secrets
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from blinking.config import default_config,validate
from blinking.ddns import update,run,request
from blinking.status import collect
from blinking.store import Store


def record(**kw):
    return dict(id='home',enabled=True,provider='cloudflare',name='home.example.com',zoneId='zone',recordId='record',teamId='',type='A',ttl=300,interval=300,source='static',address='1.1.1.1',proxied=False,**kw)


def tunnel():
    return dict(id='ssh',enabled=True,server='127.0.0.1',serverPort=7000,protocol='tcp',localAddress='127.0.0.1',localPort=8080,remotePort=6000)


class ExtensionTests(unittest.TestCase):
    def test_old_configuration_migration(self):
        c=default_config()
        for key in ('ddns','tunnels','dns','routes'):c.pop(key)
        migrated=validate(c)
        self.assertEqual(migrated['ddns'],[]);self.assertFalse(migrated['dns']['enabled'])
    def test_invalid_provider_address_and_secret_rejected(self):
        for key,value in [('provider','unknown'),('address','192.168.1.1'),('recordId','../secret'),('ttl',True),('token','secret')]:
            with self.subTest(key=key):
                c=default_config();r=record();r[key]=value;c['ddns']=[r]
                with self.assertRaises(ValueError):validate(c)
    def test_family_and_route_boundaries(self):
        c=default_config();c['ddns']=[record()];c['ddns'][0]['type']='AAAA'
        with self.assertRaises(ValueError):validate(c)
        c=default_config();c['routes']=[dict(id='downstream',enabled=True,destination='0.0.0.0/0',gateway='192.168.64.2',metric=10)]
        with self.assertRaises(ValueError):validate(c)
        c['routes'][0]['destination']='10.30.0.0/16';validate(c)
        c['routes'][0]['gateway']='192.168.64.1'
        with self.assertRaises(ValueError):validate(c)
    def test_duplicate_frp_binding_and_dns_loop(self):
        c=default_config();t=tunnel();c['tunnels']=[t,dict(t,id='second')]
        with self.assertRaises(ValueError):validate(c)
        c=default_config();c['dns']['upstreams']=['192.168.64.1']
        with self.assertRaises(ValueError):validate(c)
    def test_token_lifecycle_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory);c=store.read();c['ddns']=[record()];c['tunnels']=[tunnel()];store.save(c)
            token=secrets.token_hex(24);store.credential('ddns:home',token)
            self.assertNotIn(token.encode(),store.vault_path.read_bytes())
            with tarfile.open(fileobj=io.BytesIO(store.export())) as tar:
                self.assertIn('nix/extensions.nix',tar.getnames())
                for member in tar:self.assertNotIn(token.encode(),tar.extractfile(member).read())
                review=json.load(tar.extractfile('review.json'))
                self.assertIn('tunnels:ssh',review['missingCredentials'])
            store.delete_credential('ddns:home');self.assertNotIn('ddns:home',store.credentials_status())
            store.credential('ddns:home',token);c=store.read();c['ddns']=[];store.save(c)
            self.assertNotIn('ddns:home',store.credentials_status())


class DDNSTests(unittest.TestCase):
    def test_provider_requests_and_public_detection(self):
        calls=[]
        def transport(url,token=None,payload=None):
            calls.append((url,token,payload))
            return {'ip':'1.1.1.1'} if payload is None else {'success':True,'id':'record'}
        r=record();r['source']='public';self.assertEqual(update(r,'secret',transport)['address'],'1.1.1.1')
        self.assertIsNone(calls[0][1]);self.assertEqual(calls[1][2]['content'],'1.1.1.1')
        r['provider']='vercel';r['teamId']='team_test';update(r,'secret',transport)
        self.assertTrue(calls[-1][0].endswith('?teamId=team_test'));self.assertEqual(calls[-1][2]['value'],'1.1.1.1')
        r['type']='AAAA';r['source']='static';r['address']='2606:4700:4700::1111'
        self.assertEqual(update(r,'secret',transport)['address'],r['address'])
    def test_failures_write_status_without_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            for code in (401,403,429,500):
                def fail(*args):raise urllib.error.HTTPError('https://example.com',code,'secret-token',{},None)
                path=Path(d)/'status.json';self.assertEqual(run(record(),'secret-token',path,fail),1)
                self.assertEqual(json.loads(path.read_text())['reason'],f'http-{code}')
                self.assertNotIn('secret-token',path.read_text())
    def test_reject_provider_failure_and_bad_probe(self):
        with self.assertRaises(ValueError):update(record(),'secret',lambda *args:{'success':False})
        r=record();r['source']='public';calls=[]
        def invalid(*args):calls.append(args);return {'ip':'10.0.0.1'}
        with self.assertRaises(ValueError):update(r,'secret',invalid)
        self.assertEqual(len(calls),1)
    def test_http_transport_patch_and_redirect_block(self):
        captured=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_PATCH(self):
                captured.append((self.path,self.headers['Authorization'],json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200);self.end_headers();self.wfile.write(b'{"success":true}')
            def do_GET(self):
                self.send_response(302);self.send_header('Location','/leak');self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}/record'
            self.assertTrue(request(url,'test-token',{'content':'1.1.1.1'})['success'])
            self.assertEqual(captured,[('/record','Bearer test-token',{'content':'1.1.1.1'})])
            with self.assertRaises(urllib.error.HTTPError):request(url,'test-token')
        finally:server.shutdown();server.server_close();thread.join()


class StatusTests(unittest.TestCase):
    def test_manifest_units_ports_and_stale_ddns(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manifest=root/'manifests';manifest.mkdir();statuses=root/'state';statuses.mkdir()
            (manifest/'extensions.json').write_text(json.dumps({'services':[{'unit':'blinking-ddns-home.service','kind':'ddns','id':'home','interval':60},'missing.service']}))
            target=statuses/'blinking-ddns-home';target.mkdir();(target/'status.json').write_text(json.dumps({'state':'updated','address':'1.1.1.1','checkedAt':time.time()-1000}))
            calls=[]
            def runner(argv):
                calls.append(argv)
                if argv[0]=='ss':return 'tcp LISTEN 0 128 127.0.0.1:8787 0.0.0.0:*\n'
                return 'LoadState=loaded\nActiveState=active\nSubState=exited\nMainPID=0\n'
            result=collect(manifest,statuses,runner)
            self.assertEqual(len(result['units']),2);self.assertTrue(result['ddns'][0]['stale'])
            self.assertEqual(result['ports'][0]['address'],'127.0.0.1:8787')
            self.assertEqual(len(calls),3)
            (target/'status.json').write_text('[]')
            self.assertEqual(collect(manifest,statuses,runner)['ddns'][0]['state'],'unreadable')
    def test_bad_manifest_and_missing_tools_are_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'bad.json').write_text('{"services":["--bad"]}')
            (Path(d)/'bad-id.json').write_text('{"services":[{"unit":"test.service","id":3}]}')
            result=collect(d,d,lambda args:None)
            self.assertTrue(result['errors']);self.assertFalse(result['portsAvailable']);self.assertEqual(result['units'],[])

if __name__=='__main__':unittest.main()
