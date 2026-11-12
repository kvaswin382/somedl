"""Root-owned systemd updater: fixed packages, serialized execution and rollback."""
import fcntl
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ALLOWED={'yt-dlp':'yt-dlp[default]','gallery-dl':'gallery-dl','playwright':'playwright'}
UPDATE_LOCK=Path('/run/lock/zedair-update.lock')


def status_path(name):
    if os.geteuid()!=0 or name not in ALLOWED:raise ValueError('Unsupported update request')
    # Root must never write through a directory writable by the web process.
    state=Path('/var/lib/zedair-updates');state.mkdir(mode=0o755,exist_ok=True)
    if state.is_symlink() or state.stat().st_uid!=0 or state.stat().st_mode & 0o022:
        raise ValueError('Unsafe updater status directory')
    return state/('update-'+name+'.json')


def report(status,value,detail):
    tmp=status.with_suffix('.tmp');tmp.write_text(json.dumps({'status':value,'detail':detail,'time':time.time()}));tmp.chmod(0o644);os.replace(tmp,status)


def recover(name):
    """systemd invokes this even if the updater was killed or timed out."""
    if os.geteuid()!=0 or name not in ALLOWED:raise ValueError('Unsupported recovery request')
    with UPDATE_LOCK.open('a') as guard:
        try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return  # Another engine update still owns the environment.
        try:
            status=status_path(name)
            if status.exists() and json.loads(status.read_text()).get('status')=='running':
                report(status,'error','Update was interrupted. Service restarted; inspect the system journal and engine versions.')
        finally:subprocess.run(['systemctl','start','zedair'],check=True)


def update(name):
    status=status_path(name)
    def progress(value,detail):report(status,value,detail)
    with UPDATE_LOCK.open('a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX)
        try:old=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:old=None
        progress('running','Updating '+name+'; downloads will recover after restart')
        subprocess.run(['systemctl','stop','zedair'],check=True)
        try:
            subprocess.run([sys.executable,'-m','pip','install','--no-cache-dir','--upgrade','--index-url','https://pypi.org/simple',ALLOWED[name]],check=True,timeout=500)
            if name=='playwright':
                # Installer uses the selected installed version and refreshes its sandbox profile.
                subprocess.run(['bash','/opt/zedair/install-browser.sh','--installed'],check=True,timeout=300)
            else:
                subprocess.run([sys.executable,'-m','yt_dlp' if name=='yt-dlp' else 'gallery_dl','--version'],check=True,timeout=20)
            progress('done','Updated '+name)
        except Exception as exc:
            try:
                if old is None:raise ValueError('No previous installation to restore')
                subprocess.run([sys.executable,'-m','pip','install','--no-cache-dir',ALLOWED[name]+'=='+old],check=True,timeout=180)
                if name=='playwright':subprocess.run(['bash','/opt/zedair/install-browser.sh','--installed'],check=True,timeout=180)
                progress('error','Update failed; previous engine version restored. '+type(exc).__name__)
            except Exception:progress('error','Update and rollback failed. Run the engine installer through SSH; see system journal.')
        finally:subprocess.run(['systemctl','start','zedair'],check=True)


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--recover':recover(sys.argv[2])
    else:update(sys.argv[1] if len(sys.argv)==2 else '')
