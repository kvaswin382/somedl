"""Read-only dependency inventory and narrowly scoped maintenance actions."""
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
import re
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

UPDATES={'yt-dlp','gallery-dl','playwright'}
LEGACY_STATE=Path('/var/lib/zedair-updates')
UPDATE_UNIT=Path('/etc/systemd/system/zedair-update@.service')
LEGACY_SCRIPT=Path('/opt/zedair/engine_update.py')
UPDATE_RULE=Path('/etc/polkit-1/rules.d/49-zedair-updates.rules')


def inventory(desk,check=False):
    releases=check_releases(desk,force=check)
    packages=sorted([{'name':re.sub(r'[-_.]+','-',d.metadata.get('Name','unknown').lower()),'version':d.version} for d in importlib.metadata.distributions()],key=lambda d:d['name'].lower())
    for name in sorted(UPDATES):
        if not any(p['name'].lower()==name for p in packages):packages.append({'name':name,'version':'Not installed'})
    binaries=[]
    for name,args in [('ffmpeg',['-version']),('rclone',['version']),('deno',['--version']),('transmission-daemon',['--version'])]:
        path=shutil.which(name)
        try:
            result=subprocess.run([path,*args],capture_output=True,text=True,timeout=3) if path else None
            version=((result.stdout or result.stderr).splitlines() or ['Unknown'])[0] if result else 'Not installed'
        except (OSError,subprocess.TimeoutExpired):version='Version check unavailable'
        binaries.append({'name':name,'version':version,'path':path})
    for package in packages:
        current=releases.get('packages',{}).get(package['name'].lower(),{})
        package.update(current)
        package['has_update']=newer(current.get('latest_version'),package['version'])
        package['command']=engine_command(package['name']) if package['name'] in UPDATES and (package['has_update'] or package['version']=='Not installed') else ''
    managed={p['name']:p for p in packages if p['name'] in UPDATES}
    all_command=engine_command('all') if all(managed[n]['has_update'] for n in UPDATES) else ''
    return {'checking':releases.get('checking',False),'checked_at':releases.get('checked_at'),
            'python':sys.version.split()[0],'packages':packages,'system_tools':binaries,
            'all_command':all_command,'telegram':desk.get_setting('telegram_last_result',{})}


def engine_command(name):
    """Display-only, fixed allowlist. No subprocess or service operations."""
    if name not in UPDATES|{'all'}:raise ValueError('Unsupported engine')
    packages={'yt-dlp':"'yt-dlp[default]'",'gallery-dl':'gallery-dl','playwright':'playwright'}
    names=['yt-dlp','gallery-dl','playwright'] if name=='all' else [name]
    command='sudo /opt/zedair/venv/bin/python -m pip install --no-cache-dir --upgrade '+' '.join(packages[n] for n in names)
    if 'playwright' in names:command+=' &&\nsudo bash /opt/zedair/install-browser.sh --installed'
    return command



def newer(latest,installed):
    # Managed engines use numeric stable versions. Do not offer prereleases or
    # make an upgrade decision for an unknown/custom installed version.
    if not latest or not re.fullmatch(r'\d+(?:\.\d+)*',latest):return False
    if installed=='Not installed':return False
    if not re.fullmatch(r'\d+(?:\.\d+)*',installed):return False
    left=tuple(map(int,latest.split('.')));right=tuple(map(int,installed.split('.')))
    n=max(len(left),len(right))
    return left+(0,)*(n-len(left))>right+(0,)*(n-len(right))


def fetch_releases():
    result={}
    for package in sorted(UPDATES):
        try:
            request=urllib.request.Request('https://pypi.org/pypi/'+package+'/json',headers={'User-Agent':'Zedair release checker'})
            with urllib.request.urlopen(request,timeout=4) as response:raw=response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:raise ValueError('Release metadata too large')
            value=json.loads(raw)['info']['version']
            if not isinstance(value,str) or not re.fullmatch(r'\d+(?:\.\d+)*',value):raise ValueError('No stable release metadata')
            result[package]={'latest_version':value}
        except Exception:result[package]={'check_error':True}
    return result


def check_releases(desk,force=False):
    """Lazy asynchronous check, cached one hour; never block the UI on PyPI."""
    with desk.lock:
        if not hasattr(desk,'release_cache'):
            try:desk.release_cache=json.loads((desk.state/'engine-releases.json').read_text())
            except (OSError,ValueError):desk.release_cache={}
            desk.release_checking=False
        age=time.time()-desk.release_cache.get('checked_at',0)
        if not desk.release_checking and (age>3600 or (force and age>30)) and not desk.stop.is_set():
            desk.release_checking=True
            def work():
                result={'checked_at':time.time(),'packages':fetch_releases()}
                with desk.lock:
                    desk.release_cache=result;desk.release_checking=False
                    if not desk.stop.is_set():
                        try:
                            temp=desk.state/'engine-releases.tmp';temp.write_text(json.dumps(result));temp.chmod(0o600)
                            os.replace(temp,desk.state/'engine-releases.json')
                        except OSError:pass
            threading.Thread(target=work,daemon=True).start()
        return {**desk.release_cache,'checking':desk.release_checking}


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


def retire_update_service():
    """Root installer only: revoke access and mask the retired GUI updater."""
    if os.geteuid()!=0:raise ValueError('Root is required to remove updater integration')
    UPDATE_RULE.unlink(missing_ok=True)
    # Never interrupt pip mid-install. An active legacy operation requires attention
    # before deployment; removing the permission already prevents new web requests.
    for name in sorted(UPDATES):
        result=subprocess.run(['/usr/bin/systemctl','is-active','zedair-update@'+name+'.service'],capture_output=True,text=True,timeout=10)
        state=result.stdout.strip()
        if state not in ('inactive','failed','unknown'):
            raise ValueError('Legacy engine updater may still be running ('+name+'). Inspect sudo systemctl status zedair-update@'+name+'.service before retrying; no package operation was interrupted.')
    UPDATE_UNIT.unlink(missing_ok=True)
    UPDATE_UNIT.symlink_to('/dev/null')
    LEGACY_SCRIPT.unlink(missing_ok=True)
    (LEGACY_STATE/'integration.json').unlink(missing_ok=True)
    subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True,timeout=30)
