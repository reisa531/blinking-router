"""Local drafts, encrypted credentials, and immutable review exports."""
import io
import json
import os
import tarfile
import tempfile
import threading
from pathlib import Path
from cryptography.fernet import Fernet
from .config import ROOT, default_config, validate, diff
from .extensions import secret_refs


def atomic(path, content, mode=0o600):
    path = Path(path)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd,'wb') as f:
            f.write(content); f.flush(); os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.directory.chmod(0o700)
        self.lock = threading.RLock()
        self.path = self.directory/'router.json'
        if not self.path.exists(): atomic(self.path,json.dumps(default_config(),ensure_ascii=False).encode())
        key = self.directory/'vault.key'
        if not key.exists(): atomic(key,Fernet.generate_key())
        self.cipher = Fernet(key.read_bytes())
        self.vault_path = self.directory/'vault.enc'
        if not self.vault_path.exists(): self._write_vault({})

    def read(self):
        return validate(json.loads(self.path.read_text()))

    def _vault(self):
        return json.loads(self.cipher.decrypt(self.vault_path.read_bytes()))

    def _write_vault(self,vault):
        atomic(self.vault_path,self.cipher.encrypt(json.dumps(vault).encode()))

    def credentials_status(self):
        with self.lock:
            return {key: True for key in self._vault()}

    def credential(self,account,password):
        with self.lock:
            if account not in secret_refs(self.read()): raise ValueError('请先保存账号或服务配置')
            if not isinstance(password,str) or not 1 <= len(password) <= 1024: raise ValueError('密码长度无效')
            vault = self._vault(); vault[account] = password; self._write_vault(vault)

    def delete_credential(self,account):
        with self.lock:
            vault=self._vault();vault.pop(account,None);self._write_vault(vault)

    def save(self,data):
        with self.lock:
            config = validate(data); old = self.read()
            if config['revision'] != old['revision']: raise ValueError('配置已被另一页面修改，请刷新后重试')
            config['revision'] += 1
            atomic(self.path,json.dumps(config,ensure_ascii=False,indent=2).encode())
            vault = {k:v for k,v in self._vault().items() if k in secret_refs(config)}
            self._write_vault(vault)
            return config

    def export(self):
        with self.lock:
            config = self.read()
            missing = [w['id'] for w in config['wans'] if w['enabled'] and w['keepalive'] and w['account'] not in self._vault()]
            missing += [f'{kind}:{x["id"]}' for kind in ('ddns','tunnels') for x in config[kind] if x['enabled'] and f'{kind}:{x["id"]}' not in self._vault()]
            metadata = {'revision':config['revision'],'mode':'review-only','missingCredentials':missing,
                        'changes':diff(default_config(),config), 'secretsIncluded':False}
            files = {'router.json':json.dumps(config,ensure_ascii=False,indent=2).encode(),
                     'review.json':json.dumps(metadata,ensure_ascii=False,indent=2).encode()}
            for name in ['nix/module.nix','nix/package.nix','nix/net-loginer.nix','nix/patch-loginer.py','flake.nix','flake.lock','README.md']:
                path = ROOT/name
                if path.exists(): files[name] = path.read_bytes()
            for directory in ['blinking','examples','tests','nix']:
                for path in (ROOT/directory).rglob('*'):
                    if path.is_file() and '__pycache__' not in path.parts: files[str(path.relative_to(ROOT))] = path.read_bytes()
            out = io.BytesIO()
            with tarfile.open(fileobj=out,mode='w:gz') as tar:
                for name,body in files.items():
                    info = tarfile.TarInfo(name); info.size=len(body); info.mode=0o644
                    tar.addfile(info,io.BytesIO(body))
            return out.getvalue()
