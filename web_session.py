"""Private per-site cookie storage and a process-wide browser lease."""
import contextlib
import fcntl
import hashlib
import http.cookiejar
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit
import platforms


@contextlib.contextmanager
def lock(path):
    with path.open('a') as stream:
        os.chmod(path,0o600);fcntl.flock(stream,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


class Session:
    def __init__(self,state,url,cookie='',enabled=True):
        self.enabled=enabled
        self.root=Path(state)/'web-sessions';self.root.mkdir(mode=0o700,exist_ok=True)
        key=hashlib.sha256((urlsplit(url).hostname or '').lower().encode()).hexdigest()
        self.path=self.root/(key+'.txt');self.guard=self.root/(key+'.lock')
        self.generation=(self.root/'generation').read_text() if (self.root/'generation').exists() else ''
        self.jar=http.cookiejar.MozillaCookieJar()
        if enabled and self.path.exists():
            with lock(self.guard):self.jar=platforms.load_jar(self.path)
        if cookie:
            for c in platforms.load_jar(Path(cookie)):self.jar.set_cookie(c)
        self.original={(c.domain,c.path,c.name):c.value for c in self.jar}

    def save(self):
        if not self.enabled:return
        with lock(self.root/'sessions.lock'),lock(self.guard):
            current_generation=(self.root/'generation').read_text() if (self.root/'generation').exists() else ''
            if current_generation!=self.generation:return  # An explicit cookie deletion wins over in-flight jobs.
            current=platforms.load_jar(self.path) if self.path.exists() else http.cookiejar.MozillaCookieJar()
            for c in self.jar:
                if not c.is_expired() and (c.domain,c.path,c.name,c.value) and self.original.get((c.domain,c.path,c.name))!=c.value:
                    current.set_cookie(c)
            # First use also persists the session supplied to this job.
            if not self.path.exists():
                for c in self.jar:
                    if not c.is_expired():current.set_cookie(c)
            current.clear_expired_cookies()
            temp=self.path.with_suffix('.tmp');temp.touch(mode=0o600);temp.chmod(0o600)
            current.save(str(temp),ignore_discard=True,ignore_expires=False);os.replace(temp,self.path)


def forget(state,slot):
    root=Path(state)/'web-sessions'
    if not root.is_dir():return
    with lock(root/'sessions.lock'):
        (root/'generation').write_text(secrets.token_hex(16));(root/'generation').chmod(0o600)
        for path in root.glob('*.txt'):
            if slot=='other' or any(platforms.platform_for('https://'+c.domain.lstrip('.'))==slot for c in platforms.load_jar(path)):
                path.unlink(missing_ok=True)


def available_memory():
    try:
        values={r.split(':')[0]:int(r.split()[1])*1024 for r in Path('/proc/meminfo').read_text().splitlines() if r.split()[1].isdigit()}
        available=values.get('MemAvailable',0)
        # Host MemAvailable alone misses the service's cgroup memory ceiling.
        rel=next((r.split('::',1)[1] for r in Path('/proc/self/cgroup').read_text().splitlines() if r.startswith('0::')),None)
        if rel is not None:
            base=Path('/sys/fs/cgroup')/rel.lstrip('/')
            while base.is_relative_to(Path('/sys/fs/cgroup')):
                try:
                    maximum=(base/'memory.max').read_text().strip()
                    if maximum!='max':available=min(available,max(0,int(maximum)-int((base/'memory.current').read_text())))
                except OSError:pass
                if base==Path('/sys/fs/cgroup'):break
                base=base.parent
        return available
    except (OSError,ValueError,IndexError):return 0


@contextlib.contextmanager
def waiting_lock(path,message,heartbeat=30):
    """Wait without spinning and keep the worker watchdog informed.
    The kernel releases the lease if the worker is cancelled or crashes.
    """
    with Path(path).open('a') as stream:
        os.chmod(path,0o600)
        last=float('-inf')
        while True:
            try:
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                now=time.monotonic()
                if now-last>=heartbeat:
                    print('ZEDPROFILE '+message,flush=True);last=now
                time.sleep(min(1,heartbeat))
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


@contextlib.contextmanager
def browser_download_lease(state):
    with waiting_lock(Path(state)/'browser-download.lock','Waiting for the single browser-download slot…'):yield


@contextlib.contextmanager
def browser_lease(state):
    with waiting_lock(Path(state)/'browser.lock','Waiting for the single browser slot…'):yield
