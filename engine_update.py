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
UPDATE_LOCK=Path('/var/lib/zedair-updates/.update.lock')


def status_path(name):
    if os.geteuid()!=0 or name not in ALLOWED:raise ValueError('Unsupported update request')
    # Root must never write through a directory writable by the web process.
    state=Path('/var/lib/zedair-updates');state.mkdir(mode=0o755,exist_ok=True)
    if state.is_symlink() or state.stat().st_uid!=0 or state.stat().st_mode & 0o022:
        raise ValueError('Unsafe updater status directory')
    return state/('update-'+name+'.json')


def report(status,value,detail,**extra):
    try:previous=json.loads(status.read_text())
    except (OSError,ValueError):previous={}
    previous.update(status=value,detail=detail,time=time.time(),**extra)
    tmp=status.with_suffix('.tmp');tmp.write_text(json.dumps(previous));tmp.chmod(0o644);os.replace(tmp,status)


def command(args,timeout=60):
    from notifications import sanitized
    try:
        result=subprocess.run(args,check=True,timeout=timeout,capture_output=True,text=True)
        if isinstance(result.stdout,str) and result.stdout:print(result.stdout[-6000:],flush=True)
        return result
    except subprocess.CalledProcessError as exc:
        detail=sanitized((exc.stderr or exc.stdout or str(exc))[-4000:])
        raise RuntimeError(detail) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError('Timed out after '+str(timeout)+' seconds') from None


def restart(status):
    command(['systemctl','start','zedair'])
    saved=json.loads(status.read_text()) if status.exists() else {}
    if saved.get('restore_local_socket'):
        command(['systemctl','start','zedair-local.socket'])
        report(status,saved.get('status','error'),saved.get('detail',''),restore_local_socket=False)


def recover(name):
    """ExecStopPost also runs after SIGKILL/timeouts; restore the saved socket state."""
    if os.geteuid()!=0 or name not in ALLOWED:raise ValueError('Unsupported recovery request')
    status=status_path(name)
    with UPDATE_LOCK.open('a') as guard:
        UPDATE_LOCK.chmod(0o600)
        try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        saved=json.loads(status.read_text()) if status.exists() else {}
        if saved.get('status')=='running':
            result=os.environ.get('SERVICE_RESULT','interrupted')
            report(status,'error','Update '+result+' during '+saved.get('phase','startup')+'. '+saved.get('detail',''))
        try:restart(status)
        except Exception as exc:report(status,'error','Service restart failed: '+str(exc))


def update(name):
    status=status_path(name)
    with UPDATE_LOCK.open('a') as guard:
        UPDATE_LOCK.chmod(0o600);fcntl.flock(guard,fcntl.LOCK_EX)
        try:old=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:old=None
        # Port 80 is socket-activated. Stop the socket BEFORE the app; otherwise
        # the browser's reconnect polling cancels systemctl stop with a start job.
        socket=subprocess.run(['systemctl','is-active','--quiet','zedair-local.socket'],check=False)
        report(status,'running','Pausing downloads',phase='stop services',restore_local_socket=socket.returncode==0)
        changed=False
        try:
            if socket.returncode==0:
                command(['systemctl','stop','zedair-local.socket','zedair-local.service'])
            command(['systemctl','stop','zedair'])
            report(status,'running','Installing '+name,phase='package installation')
            changed=True
            command([sys.executable,'-m','pip','install','--no-cache-dir','--disable-pip-version-check','--upgrade','--index-url','https://pypi.org/simple',ALLOWED[name]],500)
            report(status,'running','Checking installed engine',phase='validation')
            if name=='playwright':command(['bash','/opt/zedair/install-browser.sh','--installed'],600)
            else:command([sys.executable,'-m','yt_dlp' if name=='yt-dlp' else 'gallery_dl','--version'],20)
            report(status,'done','Updated '+name,phase='complete')
        except Exception as exc:
            message=str(exc)
            if changed and old:
                try:
                    report(status,'running','Restoring previous version after: '+message,phase='rollback')
                    command([sys.executable,'-m','pip','install','--no-cache-dir',ALLOWED[name]+'=='+old],180)
                    if name=='playwright':command(['bash','/opt/zedair/install-browser.sh','--installed'],300)
                    message+=' Previous version restored.'
                except Exception as rollback:message+=' Rollback failed: '+str(rollback)
            report(status,'error',message)
        finally:
            try:restart(status)
            except Exception as exc:report(status,'error','Service restart failed: '+str(exc))


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--recover':recover(sys.argv[2])
    else:update(sys.argv[1] if len(sys.argv)==2 else '')
