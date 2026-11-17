import copy
import io
import json
import os
import secrets
import subprocess
import tarfile
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from blinking.config import default_config,validate,topology
from blinking.keepalive import transition, login, check_and_authenticate
from blinking.store import Store
from blinking.server import Server


class ConfigurationTests(unittest.TestCase):
    def test_existing_topology(self):
        c=validate(default_config()); t=topology(c)
        self.assertEqual([(w['id'],w['hostAddress'],w['peerAddress'],w['table'],w['mark']) for w in t],
            [('wan0','192.168.251.0','192.168.251.1',101,1),('wan1','192.168.251.2','192.168.251.3',102,2)])
    def test_removed_slot_does_not_renumber(self):
        c=default_config(); c['wans'].pop(0)
        self.assertEqual(topology(validate(c))[0]['table'],102)
    def test_reject_duplicate_mac(self):
        c=default_config(); c['wans'][1]['mac']=c['wans'][0]['mac'].upper()
        with self.assertRaisesRegex(ValueError,'重复'):validate(c)
    def test_reject_multicast_mac(self):
        c=default_config();c['wans'][0]['mac']='01:00:00:00:00:01'
        with self.assertRaisesRegex(ValueError,'单播'):validate(c)
    def test_account_reference_and_enable(self):
        c=default_config();c['wans'][0]['keepalive']=True
        with self.assertRaisesRegex(ValueError,'账号'):validate(c)
        c['accounts']=[{'id':'a','name':'Test','username':'user'}];c['wans'][0]['account']='a'
        self.assertTrue(validate(c)['wans'][0]['keepalive'])
    def test_address_pool_and_reservation(self):
        c=default_config();c['dhcp']['start']=100;c['dhcp']['size']=200
        with self.assertRaisesRegex(ValueError,'地址池'):validate(c)
        c=default_config();c['dhcp']['reservations']=[{'name':'x','mac':'02:00:00:00:00:01','address':'192.168.64.1'}]
        with self.assertRaisesRegex(ValueError,'可用范围'):validate(c)
    def test_nix_injection_rejected(self):
        c=default_config();c['network']['parent']='x"; bad = true;'
        with self.assertRaises(ValueError):validate(c)
    def test_plaintext_password_not_config(self):
        c=default_config();c['accounts']=[{'id':'a','name':'x','username':'x','password':'x'}]
        with self.assertRaises(ValueError):validate(c)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_password_encryption_and_export(self):
        c=self.store.read();c['accounts']=[{'id':'test','name':'Test','username':'test-user'}]
        self.store.save(c);secret=secrets.token_hex(24);self.store.credential('test',secret)
        self.assertNotIn(secret.encode(),self.store.vault_path.read_bytes())
        self.assertEqual(os.stat(self.store.vault_path).st_mode & 0o777,0o600)
        self.assertEqual(Store(self.temp.name).credentials_status(),{'test':True})
        with tarfile.open(fileobj=io.BytesIO(self.store.export())) as tar:
            self.assertIn('router.json',tar.getnames())
            for member in tar:
                content=tar.extractfile(member).read()
                self.assertNotIn(secret.encode(),content)
                self.assertNotIn('vault',member.name)
    def test_stale_save(self):
        c=self.store.read();self.store.save(c)
        with self.assertRaisesRegex(ValueError,'另一页面'):self.store.save(c)
    def test_deleted_account_removes_secret(self):
        c=self.store.read();c['accounts']=[{'id':'a','name':'a','username':'a'}]
        c=self.store.save(c);self.store.credential('a','x');c['accounts']=[];self.store.save(c)
        self.assertEqual(self.store.credentials_status(),{})


class HealthTests(unittest.TestCase):
    def test_network_failure_does_not_send_credentials(self):
        def unexpected(*args): self.fail('Network failure must not trigger login')
        self.assertEqual(check_and_authenticate(3,'unused',lambda _: 'network_error',unexpected),'network_error')
        self.assertEqual(check_and_authenticate(3,'unused',lambda _: 'online',unexpected),'online')
    def test_portal_login_requires_post_login_probe(self):
        probes=iter(['portal','online']); calls=[]
        def authenticate(*args): calls.append(args); return 'success'
        self.assertEqual(check_and_authenticate(3,'test',lambda _: next(probes),authenticate),'online')
        self.assertEqual(calls,[('test',90)])
    def test_login_failure_keeps_specific_state(self):
        self.assertEqual(check_and_authenticate(3,'test',lambda _: 'portal',lambda *_:'invalid_password'),'invalid_password')
    def test_recovery_resets_backoff(self):
        s=transition({},'network_error',100)
        s=transition(s,'network_error',200)
        self.assertEqual(s['nextCheck'],320)
        s=transition(s,'online',300)
        self.assertEqual(s['failures'],0);self.assertEqual(s['nextCheck'],360)
    def test_invalid_credentials_pause(self):
        for event in ['invalid_password','locked']:
            s=transition({},event,10)
            self.assertTrue(s['paused']);self.assertIsNone(s['nextCheck']);self.assertFalse(s['eligible'])
    def test_threshold_and_cap(self):
        s=transition({},'online',0)
        s=transition(s,'network_error',1);self.assertTrue(s['eligible'])
        s=transition(s,'network_error',2);s=transition(s,'network_error',3);self.assertFalse(s['eligible'])
        for _ in range(20):s=transition(s,'network_error',100)
        self.assertEqual(s['nextCheck'],1000)


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.server=Server(('127.0.0.1',0),Store(self.temp.name))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def request(self,path,data=None,headers=None):
        h={'Content-Type':'application/json','X-CSRF-Token':self.server.csrf};h.update(headers or {})
        return urllib.request.urlopen(urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=h))
    def test_roundtrip_and_persistence(self):
        with self.request('/api/state') as r:state=json.load(r)
        c=state['config'];c['wans'][1]['enabled']=False
        with self.request('/api/config',c) as r:saved=json.load(r)
        self.assertEqual(saved['config']['revision'],1)
        self.assertFalse(self.server.store.read()['wans'][1]['enabled'])
    def test_cross_origin_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/config',default_config(),{'Origin':'https://evil.invalid'})
        self.assertEqual(e.exception.code,403)
    def test_dns_rebinding_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/state',headers={'Host':'evil.invalid'})
        self.assertEqual(e.exception.code,403)
    def test_export_and_no_apply(self):
        with self.request('/api/export') as r:self.assertEqual(r.headers['Content-Type'],'application/gzip')
        with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/apply',{})
        self.assertEqual(e.exception.code,404)
    def test_no_secret_in_api(self):
        c=self.server.store.read();c['accounts']=[{'id':'a','name':'a','username':'a'}];self.server.store.save(c)
        self.server.store.credential('a','sensitive-test-value')
        with self.request('/api/state') as r:self.assertNotIn(b'sensitive-test-value',r.read())

if __name__=='__main__':unittest.main()
