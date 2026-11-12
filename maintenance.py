"""Read-only dependency inventory and narrowly scoped maintenance actions."""
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

UPDATES={'yt-dlp','gallery-dl','playwright'}
UPDATE_STATE=Path('/var/lib/zedair-updates')
UPDATE_RULE=Path('/etc/polkit-1/rules.d/49-zedair-updates.rules')


def inventory(desk):
    packages=sorted([{'name':d.metadata.get('Name','unknown'),'version':d.version} for d in importlib.metadata.distributions()],key=lambda d:d['name'].lower())
    for name in sorted(UPDATES):
        if not any(p['name'].lower()==name for p in packages):packages.append({'name':name,'version':'Not installed'})
    binaries=[]
    for name,args in [('ffmpeg',['-version']),('deno',['--version']),('transmission-daemon',['--version'])]:
        path=shutil.which(name)
        try:
            result=subprocess.run([path,*args],capture_output=True,text=True,timeout=3) if path else None
            version=((result.stdout or result.stderr).splitlines() or ['Unknown'])[0] if result else 'Not installed'
        except (OSError,subprocess.TimeoutExpired):version='Version check unavailable'
        binaries.append({'name':name,'version':version,'path':path})
    status={}
    for name in UPDATES:
        path=UPDATE_STATE/('update-'+name+'.json')
        try:status[name]=json.loads(path.read_text())
        except (OSError,ValueError):pass
    return {'python':sys.version.split()[0],'packages':packages,'system_tools':binaries,'updates':status,
            'update_available':UPDATE_RULE.is_file(),
            'telegram':desk.get_setting('telegram_last_result',{})}


def request_update(desk,package):
    if package not in UPDATES:raise ValueError('Only yt-dlp, gallery-dl and Playwright are managed by this updater')
    # No URL, shell expression, arbitrary package name or user-defined command is accepted.
    result=subprocess.run(['/usr/bin/systemctl','--no-ask-password','start','--no-block',f'zedair-update@{package}.service'],capture_output=True,text=True,timeout=10)
    if result.returncode:raise ValueError('Managed updater is unavailable. Run upgrade.sh on the laptop to install its restricted service.')
    return {'ok':True,'message':'Update scheduled. The service will pause, update and restart; refresh after reconnecting.'}


def clear_cache(desk,data):
    removed=0;freed=0
    with desk.file_lock,desk.lock:
        active=set(desk.workers)|set(desk.processes)
        root=desk.root/'.incomplete'
        if root.is_dir() and not root.is_symlink():
            for stage in root.iterdir():
                if not stage.is_dir() or stage.is_symlink() or stage.name in active:continue
                jobs=desk.rows('SELECT status FROM jobs WHERE id=?',(stage.name,))
                # Partial files and archives are never ordinary cache.
                if data.get('remove_abandoned') is True and data.get('confirm') is True and (not jobs or jobs[0]['status']=='cancelled'):
                    for p in stage.rglob('*'):
                        if p.is_file() and not p.is_symlink():freed+=p.stat().st_size
                    shutil.rmtree(stage);removed+=1;continue
                if jobs and jobs[0]['status'] in ('queued','running','retry','blocked','paused'):continue
                for name in ('.browser-error.jpg','.gallery-config.json','.engine-cookies.txt','.browser-cookies.txt'):
                    p=stage/name
                    if p.is_file() and not p.is_symlink():freed+=p.stat().st_size;p.unlink();removed+=1
        cache=desk.state/'favicon-cache'
        if cache.is_dir() and not cache.is_symlink():
            for p in cache.iterdir():
                if p.is_file() and not p.is_symlink():freed+=p.stat().st_size;p.unlink();removed+=1
    return {'removed':removed,'bytes':freed,'message':'Cleared safe caches. Cookies, downloaded files and resumable partials were preserved unless abandoned cleanup was explicitly selected.'}


def favicon(desk,jid):
    import download_http as http
    job=desk.job(jid);p=urlsplit(job['url']);origin=p.scheme+'://'+p.netloc
    root=desk.state/'favicon-cache';root.mkdir(mode=0o700,exist_ok=True)
    key=hashlib.sha256(origin.encode()).hexdigest();path=root/key
    if path.is_file() and time.time()-path.stat().st_mtime<86400:return path.read_bytes(),'image/x-icon'
    try:
        with http.request(http.opener(),origin+'/favicon.ico',timeout=4) as response:
            body=response.read(65537)
            if len(body)>65536 or not (body.startswith(b'\x00\x00\x01\x00') or body.startswith(b'\x89PNG') or body.startswith(b'\xff\xd8')):raise ValueError('Not a raster icon')
        temp=path.with_suffix('.tmp');temp.write_bytes(body);os.replace(temp,path)
        return body,'image/x-icon'
    except Exception:
        # Negative cache avoids repeatedly fetching absent favicons on queue redraw.
        path.write_bytes(b'');return b'','image/x-icon'


def install_update_service():
    """Called by the root installer/upgrader only; the web process cannot edit it."""
    if os.geteuid()!=0:raise ValueError('Root is required to install updater integration')
    unit=Path('/etc/systemd/system/zedair-update@.service')
    unit.write_text('''[Unit]
Description=Zedair managed engine update (%i)
After=network-online.target
[Service]
Type=oneshot
ExecStart=/opt/zedair/venv/bin/python /opt/zedair/engine_update.py %i
TimeoutStartSec=900
UMask=0077
''')
    unit.chmod(0o644)
    # Authorization is performed by systemd, retaining NoNewPrivileges=true.
    if not shutil.which('polkitd') and not Path('/usr/lib/polkit-1/polkitd').exists():
        subprocess.run(['apt-get','install','-y','polkitd'],check=True)
    UPDATE_RULE.parent.mkdir(parents=True,exist_ok=True)
    units=json.dumps(['zedair-update@'+name+'.service' for name in sorted(UPDATES)])
    UPDATE_RULE.write_text('polkit.addRule(function(action, subject) {\n'
        '  if (subject.user === "zedair" && action.id === "org.freedesktop.systemd1.manage-units" &&\n'
        '      action.lookup("verb") === "start" && '+units+'.indexOf(action.lookup("unit")) !== -1) {\n'
        '    return polkit.Result.YES;\n  }\n});\n')
    UPDATE_RULE.chmod(0o644)
    UPDATE_STATE.mkdir(mode=0o755,exist_ok=True)
    os.chown(UPDATE_STATE,0,0);UPDATE_STATE.chmod(0o755)
    subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True)
