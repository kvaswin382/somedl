"""Root-only, fixed systemd operations. No browser-provided commands or paths."""
import json
import os
import pwd
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
STATE=Path('/var/lib/zedair')
STATUS=Path('/var/lib/zedair-updates/system.json')


def settings():
    p=STATE/'desk.sqlite3'
    if p.is_symlink() or p.stat().st_uid!=pwd.getpwnam('zedair').pw_uid:raise ValueError('Unsafe settings database')
    with sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True) as db:
        return {k:json.loads(v) for k,v in db.execute('SELECT key,value FROM settings')},db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]


def execute(action):
    if os.geteuid()!=0 or action not in ('memory','reboot'):raise ValueError('Unsupported system operation')
    s,running=settings()
    if action=='memory':
        limit=s.get('torrent_memory_mib',1024)
        if type(limit) is not int or not 128<=limit<=1536:raise ValueError('Invalid torrent memory limit')
        p=subprocess.run(['/usr/bin/systemctl','show','zedair-transmission','--property=MemoryCurrent','--value'],capture_output=True,text=True,check=True)
        current=int(p.stdout.strip()) if p.stdout.strip().isdigit() else 0
        if current>limit*1024**2*.8:raise ValueError('Memory limit deferred: current torrent usage is too high to lower it safely. Pause torrents and save again.')
        subprocess.run(['/usr/bin/systemctl','set-property','zedair-transmission.service',f'MemoryHigh={int(limit*.8)}M',f'MemoryMax={limit}M','MemorySwapMax=256M'],check=True)
        return 'Torrent memory ceiling applied: '+str(limit)+' MiB'
    requested=s.get('reboot_requested_at',0)
    if not s.get('hold') or not 0<=time.time()-requested<60 or running:raise ValueError('Reboot skipped: application is not idle or request expired')
    from server import Transmission
    config=json.loads(Path('/etc/zedair/config.json').read_text())
    items=Transmission(config).call('torrent-get',{'fields':['status']}).get('torrents',[])
    if any(t['status']!=0 for t in items):raise ValueError('Reboot skipped: torrents are active')
    subprocess.run(['/usr/bin/systemctl','--no-block','reboot'],check=True)
    return 'Reboot requested'


def install():
    if os.geteuid()!=0:raise ValueError('Root required')
    for action in ('memory','reboot'):
        p=Path('/etc/systemd/system/zedair-'+action+'.service')
        p.write_text('[Unit]\nDescription=Zedair '+action+' control\n[Service]\nType=oneshot\nExecStart=/opt/zedair/venv/bin/python /opt/zedair/system_control.py '+action+'\nTimeoutStartSec=45\nUMask=0077\n')
        p.chmod(0o644)
    p=Path('/etc/polkit-1/rules.d/49-zedair-system.rules')
    p.write_text('polkit.addRule(function(action, subject) {\n if (subject.user === "zedair" && action.id === "org.freedesktop.systemd1.manage-units" && action.lookup("verb") === "start" && ["zedair-memory.service","zedair-reboot.service"].indexOf(action.lookup("unit")) !== -1) return polkit.Result.YES;\n});\n')
    p.chmod(0o644)
    # Upgrade the old 300 MiB unit without replacing mount/security directives.
    drop=Path('/etc/systemd/system/zedair-transmission.service.d/20-memory.conf');drop.parent.mkdir(exist_ok=True)
    try:s,_=settings();limit=max(128,min(1536,int(s.get('torrent_memory_mib',1024))))
    except (OSError,sqlite3.Error):limit=1024
    drop.write_text(f'[Service]\nMemoryHigh={int(limit*.8)}M\nMemoryMax={limit}M\nMemorySwapMax=256M\n');drop.chmod(0o644)
    subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True)


if __name__=='__main__':
    try:detail=execute(sys.argv[1] if len(sys.argv)==2 else '');status='done'
    except Exception as exc:detail=str(exc);status='error'
    if os.geteuid()==0:
        parent=STATUS.parent
        if parent.is_dir() and not parent.is_symlink() and parent.stat().st_uid==0 and not parent.stat().st_mode&0o022:
            tmp=STATUS.with_suffix('.tmp');tmp.write_text(json.dumps({'status':status,'detail':detail,'time':time.time()}));tmp.chmod(0o644);os.replace(tmp,STATUS)
    if status=='error':raise SystemExit(detail)
