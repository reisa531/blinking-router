"""Read declared units in one manifest directory and query local runtime state."""
import datetime
import json
import re
import subprocess
import time
from pathlib import Path

UNIT = re.compile(r'[A-Za-z0-9_@.:-]+\.(?:service|timer)')


def command(argv):
    try:
        result=subprocess.run(argv,capture_output=True,text=True,timeout=3)
        return result.stdout if result.returncode==0 else None
    except (FileNotFoundError,subprocess.TimeoutExpired):
        return None


def collect(manifest_dir, status_dir, runner=command):
    declarations={}; errors=[]
    for path in sorted(Path(manifest_dir).glob('*.json')):
        try:
            manifest=json.loads(path.read_text())
            for raw in manifest['services']:
                entry={'unit':raw,'kind':'system'} if isinstance(raw,str) else raw
                if not isinstance(entry,dict) or not UNIT.fullmatch(entry.get('unit','')): raise ValueError()
                if not isinstance(entry.get('id',''),str): raise ValueError()
                declarations[entry['unit']]=entry
        except (OSError,ValueError,KeyError,TypeError):
            errors.append('无法读取清单 '+path.name)
    units=[]
    for unit, entry in declarations.items():
        raw=runner(['systemctl','show',unit,'--no-pager','--property=LoadState,ActiveState,SubState,MainPID'])
        properties=dict(line.split('=',1) for line in (raw or '').splitlines() if '=' in line)
        units.append({'unit':unit,'kind':entry.get('kind','system'),'id':entry.get('id',''),
                      'load':properties.get('LoadState','unknown'),'active':properties.get('ActiveState','unknown'),
                      'sub':properties.get('SubState','unknown'),'pid':properties.get('MainPID','0')})
    raw=runner(['ss','-H','-lntu'])
    ports=[]
    for line in (raw or '').splitlines():
        fields=line.split()
        if len(fields)>=5: ports.append({'protocol':fields[0],'state':fields[1],'address':fields[4]})
    ddns=[]
    # Only read status paths for IDs explicitly declared in the manifest.
    for entry in declarations.values():
        ident=entry.get('id','')
        if entry.get('kind')!='ddns' or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',ident):continue
        path=Path(status_dir)/('blinking-ddns-'+ident)/'status.json'
        row={'id':ident,'state':'not-observed'}
        try:
            data=json.loads(path.read_text())
            if not isinstance(data,dict): raise ValueError()
            row.update({k:data[k] for k in ('state','address','checkedAt','reason') if k in data})
            age=time.time()-float(data.get('checkedAt',0))
            row['stale']=age>max(180,int(entry.get('interval',300))*2)
        except FileNotFoundError:pass
        except (OSError,ValueError,TypeError):row['state']='unreadable'
        ddns.append(row)
    return {'collectedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'local',
            'manifestDirectory':str(manifest_dir),'units':units,'ports':ports,'portsAvailable':raw is not None,
            'ddns':ddns,'errors':errors}
