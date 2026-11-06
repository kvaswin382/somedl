#!/usr/bin/env python3
"""Zedair Download Desk — a small, single-user LAN download server."""
import argparse
import base64
import collections
import hashlib
import hmac
import http.cookies
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from media import build_command, engine_for, validate_url
import platforms
import profiles
from systemstats import SystemStats
from publishing import publish, MEDIA
from speedcheck import SpeedTest, history_csv

HERE = Path(__file__).resolve().parent
DAYS = 35
DEFAULTS = {'media_kib': 0, 'torrent_down_kib': 0, 'torrent_up_kib': 256,
            'seed_ratio': 0.0, 'reserve_gib': 5, 'playlist_limit': 100, 'retry_count': 3,
            'hold': False}
QUALITY = ('best', '2160', '1080', '720', '480', 'audio')
COOKIE_SLOTS = tuple(platforms.DEFAULTS)
ACTIVE = ('queued', 'running', 'retry', 'blocked', 'paused')


PREVIEW_MIME={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp',
    '.gif':'image/gif','.avif':'image/avif','.bmp':'image/bmp',
    '.mp4':'video/mp4','.m4v':'video/mp4','.webm':'video/webm','.ogv':'video/ogg',
    '.mov':'video/quicktime','.mkv':'video/x-matroska',
    '.mp3':'audio/mpeg','.m4a':'audio/mp4','.ogg':'audio/ogg','.opus':'audio/ogg','.wav':'audio/wav','.flac':'audio/flac'}

def preview_type(suffix):
    mime=PREVIEW_MIME.get(suffix.lower(),'')
    return mime.split('/')[0] if mime else ''

def parse_range(header,size):
    if header is None:return 0,size-1
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',header.strip())
    if not match or size==0 or not any(match.groups()):raise ValueError('Invalid range')
    first,last=match.groups()
    if not first:
        count=int(last)
        if count<=0:raise ValueError('Invalid suffix range')
        return max(0,size-count),size-1
    start=int(first);end=min(int(last),size-1) if last else size-1
    if start>=size or end<start:raise ValueError('Range outside file')
    return start,end


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return time.time()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + value


def password_ok(password, saved):
    return hmac.compare_digest(password_hash(password, saved.split(':')[0]), saved)


def safe_relative(raw):
    if not isinstance(raw, str) or len(raw) > 1024:
        raise APIError('Folder path is too long')
    raw = raw.strip().replace('\\', '/')
    if raw in ('', '.'):
        return ''
    parts = raw.split('/')
    reserved = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)
    for part in parts:
        if not part or part in ('.', '..') or part.startswith('.') or part.endswith((' ', '.')) or len(part.encode()) > 150:
            raise APIError('Use normal folder names without hidden, empty or parent-directory segments')
        if re.search(r'[\x00-\x1f<>:"|?*]', part) or reserved.match(part):
            raise APIError('This name is not compatible with Windows/exFAT')
    return '/'.join(parts)


class Transmission:
    def __init__(self, config):
        self.url = config.get('rpc_url', 'http://127.0.0.1:9092/transmission/rpc')
        self.auth = base64.b64encode((config.get('rpc_user', 'zedair') + ':' + config.get('rpc_password', '')).encode()).decode()
        self.sid = ''
        self.lock = threading.RLock()

    def call(self, method, arguments=None):
        with self.lock:
            for _ in range(2):
                req = urllib.request.Request(self.url, json.dumps({'method': method, 'arguments': arguments or {}}).encode(),
                      {'Content-Type': 'application/json', 'Authorization': 'Basic ' + self.auth,
                       'X-Transmission-Session-Id': self.sid})
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:
                        data = json.load(response)
                    if data.get('result') != 'success':
                        raise APIError('Torrent engine: ' + str(data.get('result', 'unknown response')), 502)
                    return data.get('arguments', {})
                except urllib.error.HTTPError as exc:
                    if exc.code == 409:
                        self.sid = exc.headers.get('X-Transmission-Session-Id', '')
                        continue
                    raise APIError('Torrent engine rejected the request. Check its service and credentials.', 503)
                except (OSError, ValueError):
                    raise APIError('Torrent engine is offline. Check zedair-transmission on the laptop.', 503)
            raise APIError('Torrent engine session handshake failed', 503)


class Desk:
    def __init__(self, config):
        self.config = config
        self.state = Path(config['state']).resolve()
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.mount = Path(config['mount']).resolve()
        self.root = self.mount / 'Zedair'
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.state / 'desk.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS admin(id INTEGER PRIMARY KEY CHECK(id=1), username TEXT, password TEXT);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, csrf TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, url TEXT, kind TEXT, folder TEXT, name TEXT, subdir TEXT,
                quality TEXT, playlist INTEGER, subtitles INTEGER, cookie_profile TEXT,
                status TEXT, attempts INTEGER DEFAULT 0, next_try REAL DEFAULT 0,
                created REAL, updated REAL, progress REAL DEFAULT 0, detail TEXT DEFAULT '',
                last_error TEXT DEFAULT '', position REAL);
        ''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(jobs)')}
        for key, definition in {'container':"TEXT DEFAULT 'auto'", 'layout':'INTEGER DEFAULT 1', 'phase':"TEXT DEFAULT 'download'", 'error_kind':"TEXT DEFAULT ''", 'profile_mode':"TEXT DEFAULT ''", 'profile_username':"TEXT DEFAULT ''", 'scroll_limit':'INTEGER DEFAULT 2000', 'items':'INTEGER DEFAULT 0'}.items():
            if key not in columns:
                self.db.execute('ALTER TABLE jobs ADD COLUMN '+key+' '+definition)
        self.db.execute('CREATE TABLE IF NOT EXISTS published(job TEXT, source TEXT, target TEXT, signature TEXT, complete INTEGER, PRIMARY KEY(job,source))')
        self.db.execute("UPDATE jobs SET status='queued', detail='Recovering after service restart' WHERE status='running'")
        self.db.commit()
        self.stop = threading.Event()
        self.current = None
        self.process = None
        self.live = {}
        self.torrents = []
        self.torrent_error = 'Connecting to torrent engine…'
        self.started = now()
        self.failures = collections.defaultdict(collections.deque)
        self.rpc = Transmission(config)
        self.threads = []
        self.speedtest = SpeedTest(self)
        self.systemstats = SystemStats()

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def write(self, sql, args=()):
        with self.lock:
            self.db.execute(sql, args)
            self.db.commit()

    def get_setting(self, key, fallback=None):
        rows = self.rows('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(rows[0]['value']) if rows else fallback

    def set_setting(self, key, value):
        self.write('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def settings(self):
        return {k: self.get_setting(k, v) for k, v in DEFAULTS.items()}

    def platform_settings(self):
        return platforms.preferences(self.get_setting('platforms', {}))

    def staging(self, jid):
        if not re.fullmatch('[a-f0-9]{12}', jid):
            raise APIError('Invalid job identifier')
        if not self.storage()['ready']:
            raise APIError(self.storage()['reason'])
        base = self.root / '.incomplete'
        path = base / jid
        if base.is_symlink() or path.is_symlink():
            raise APIError('Partial download folder must not be a symbolic link')
        path.mkdir(parents=True, exist_ok=True)
        return path

    def delete_file(self, data):
        if data.get('confirm') is not True:
            raise APIError('Confirm permanent deletion first')
        with self.lock:
            path = self.destination(data.get('path',''), reserve=False)
            if not path.is_file():
                raise APIError('File not found',404)
            stamp = str(path.stat().st_size)+':'+str(path.stat().st_mtime_ns)
            if data.get('stamp') != stamp:
                raise APIError('File changed. Refresh the file list before deleting.',409)
            for job in self.rows("SELECT * FROM jobs WHERE status IN ('running','queued','retry','paused','blocked')"):
                old = self.root / job['folder'] / job['subdir']
                if job['layout']==1 and path.is_relative_to(old):
                    raise APIError('Cancel the download before deleting its files',409)
            reserved = self.rows('SELECT p.target,j.folder,j.subdir FROM published p JOIN jobs j ON p.job=j.id WHERE p.complete=0')
            if any(path==self.root/r['folder']/r['subdir']/r['target'] for r in reserved):
                raise APIError('This file is still being saved',409)
            torrents = self.rpc.call('torrent-get',{'fields':['downloadDir','files']}).get('torrents',[])
            for torrent in torrents:
                if any(path.resolve()==(Path(torrent['downloadDir'])/f['name']).resolve() for f in torrent.get('files',[])):
                    raise APIError('Remove this torrent from the torrent list first, then delete its files here',409)
            path.unlink()

    def storage(self, reserve=True):
        status = {'ready': False, 'root': str(self.root), 'mount': str(self.mount), 'free': 0, 'total': 0, 'reason': ''}
        try:
            if self.config.get('require_mount', True):
                if not os.path.ismount(self.mount):
                    raise APIError('SSD is not mounted. Downloads are waiting safely.')
                uuid = self.config.get('ssd_uuid')
                if not uuid:
                    raise APIError('SSD identity is not configured')
                device = Path('/dev/disk/by-uuid') / uuid
                if not device.exists() or device.stat().st_rdev != self.mount.stat().st_dev:
                    raise APIError('The mounted drive does not match your configured SSD')
            if not self.root.is_dir() or self.root.is_symlink():
                raise APIError('The Zedair folder is missing or is a symbolic link')
            disk = shutil.disk_usage(self.root)
            status.update(free=disk.free, total=disk.total)
            if reserve and disk.free < self.get_setting('reserve_gib', 5) * 1024**3:
                raise APIError('SSD free space is below the configured reserve')
            if not os.access(self.root, os.W_OK):
                raise APIError('Zedair folder is not writable by the service user')
            status['ready'] = True
        except (OSError, APIError) as exc:
            status['reason'] = str(exc)
        return status

    def destination(self, relative='', create=False, check=True, reserve=True):
        if check:
            status = self.storage(reserve=reserve)
            if not status['ready']:
                raise APIError(status['reason'], 409)
        relative = safe_relative(relative)
        path = self.root / relative
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise APIError('Folder is outside Zedair', 403)
        current = self.root
        for part in relative.split('/') if relative else []:
            current /= part
            if current.is_symlink():
                raise APIError('Symbolic links are not allowed', 403)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def job(self, jid):
        rows = self.rows('SELECT * FROM jobs WHERE id=?', (jid,))
        if not rows:
            raise APIError('Download not found', 404)
        return rows[0]

    def update_job(self, jid, **fields):
        fields['updated'] = now()
        with self.lock:
            self.db.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?', (*fields.values(), jid))
            self.db.commit()

    def add_jobs(self, data):
        urls = data.get('urls', [])
        if not isinstance(urls, list) or not 1 <= len(urls) <= 30:
            raise APIError('Paste between 1 and 30 URLs')
        if len(self.rows('SELECT id FROM jobs')) + len(urls) > 1000:
            raise APIError('History is full (1,000 jobs). Clear finished history first.')
        quality = data.get('quality', 'platform')
        if quality not in (*QUALITY, 'platform'):
            raise APIError('Unknown quality preset')
        container = data.get('container','platform')
        if container not in ('platform','auto','mp4'):
            raise APIError('Choose automatic format or MP4 only')
        cookie = data.get('cookie_profile', 'auto')
        if cookie not in (*COOKIE_SLOTS, 'auto', 'none'):
            raise APIError('Unknown cookie profile')
        folder = safe_relative(data.get('folder', ''))
        name = safe_relative(data.get('name', ''))
        if '/' in name or len(name) > 80:
            raise APIError('Use one short display label')
        # Validate the whole batch before recording any work. Queuing while unplugged is allowed.
        clean = []
        for url in urls:
            url = url.strip()
            validate_url(url, dns=False)
            kind = engine_for(url)
            if kind == 'instagram' and not re.search(r'/(p|reel|reels|tv)/[^/]+', urllib.parse.urlsplit(url).path):
                raise APIError('Instagram requires a direct post, carousel or reel URL')
            if kind == 'threads' and not re.search(r'/(post|t)/[\w-]+', urllib.parse.urlsplit(url).path):
                raise APIError('Threads requires a direct post URL')
            if not self.platform_settings()[platforms.platform_for(url)]['enabled']:
                raise APIError('This platform is disabled in Settings')
            safe_relative(platforms.platform_folder(url,self.platform_settings()))
            clean.append((url, kind))
        result = []
        with self.lock:
            for url, kind in clean:
                # Duplicate checks apply to unfinished jobs only; explicit redownloads stay possible.
                if self.db.execute("SELECT id FROM jobs WHERE url=? AND status IN ('queued','running','retry','paused','blocked')", (url,)).fetchone():
                    continue
                jid = secrets.token_hex(6)
                prefs = self.platform_settings()[platforms.platform_for(url)]
                subdir = platforms.platform_folder(url,self.platform_settings())
                selected_quality = prefs.get('quality','best') if quality=='platform' else quality
                self.db.execute('''INSERT INTO jobs(id,url,kind,folder,name,subdir,quality,playlist,subtitles,cookie_profile,status,created,updated,position,layout)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,2)''',
                    (jid, url, kind, folder, name or urllib.parse.urlsplit(url).hostname, subdir, selected_quality,
                     int(bool(data.get('playlist') or (quality=='platform' and prefs.get('playlist')))), int(bool(data.get('subtitles') or (quality=='platform' and prefs.get('subtitles')))), cookie, 'queued', now(), now(), now()))
                selected_container=prefs.get('container','auto') if container=='platform' else container
                self.db.execute('UPDATE jobs SET container=? WHERE id=?',(selected_container,jid))
                result.append(jid)
            self.db.commit()
        return {'ids': result, 'skipped': len(urls)-len(result)}

    def add_profile(self, data):
        platform=data.get('platform')
        username,url=profiles.profile_target(platform,data.get('profile',''))
        mode=data.get('mode','everything')
        if mode not in profiles.MODES:raise APIError('Choose posts, reels or everything')
        limit=data.get('scroll_limit',2000)
        if type(limit) is not int or not 10<=limit<=10000:raise APIError('Scroll limit must be between 10 and 10,000')
        if not self.platform_settings()[platform]['enabled']:raise APIError('Enable this platform in Settings first')
        if not platforms.cookie_status(self.state,platform)['usable']:raise APIError('Add a usable '+platform+' session in Settings → Platforms first')
        if platform=='facebook' and not profiles.browser_ready():raise APIError('Install the optional collector on the laptop: sudo bash /opt/zedair/install-facebook.sh')
        folder=safe_relative(data.get('folder',''))
        subdir=safe_relative(('Instagram' if platform=='instagram' else 'Facebook')+'/'+username)
        with self.lock:
            if self.db.execute("SELECT id FROM jobs WHERE url=? AND profile_mode=? AND status IN ('queued','running','retry','paused','blocked')",(url,mode)).fetchone():
                raise APIError('This profile and selection are already in the queue')
            if self.db.execute('SELECT count(*) FROM jobs').fetchone()[0]>=1000:raise APIError('History is full. Clear finished jobs first.')
            jid=secrets.token_hex(6)
            self.db.execute("""INSERT INTO jobs(id,url,kind,folder,name,subdir,quality,playlist,subtitles,cookie_profile,status,created,updated,position,layout,profile_mode,profile_username,scroll_limit)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,2,?,?,?)""",(jid,url,engine_for(url),folder,platform.title()+' / '+username,subdir,'best',0,0,platform,'queued',now(),now(),now(),mode,username,limit))
            self.db.commit()
        return {'id':jid,'destination':'/'.join(x for x in ('Zedair',folder,subdir) if x)}

    def cookie_for(self, job):
        slot = job['cookie_profile']
        if slot == 'none':
            return None
        if slot == 'auto':
            slot = platforms.platform_for(job['url'])
        path = self.state / ('cookies-' + slot + '.txt')
        return path if path.exists() else None

    def clean_metadata(self, stage):
        try:
            if not self.storage(reserve=False)['ready'] or stage.is_symlink():
                return
            for item in stage.glob('*.json'):
                if item.is_file() and not item.is_symlink():
                    item.unlink()
            if not any(stage.iterdir()):
                stage.rmdir()
        except OSError:
            pass  # Publication has completed; optional metadata cleanup must not change its status.

    def stop_process(self):
        process = self.process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def job_action(self, jid, action):
        with self.lock:
            job = self.job(jid)
            if action in ('pause', 'cancel'):
                self.update_job(jid, status='paused' if action == 'pause' else 'cancelled', detail='Paused' if action == 'pause' else 'Cancelled; partial files kept')
                # The worker notices the new state and stops its complete process group.
            elif action in ('resume', 'retry'):
                if job['status'] == 'running':
                    raise APIError('This download is already running')
                self.update_job(jid, status='queued', attempts=0, next_try=0, last_error='', error_kind='', detail='Waiting in queue')
            elif action == 'first':
                self.update_job(jid, position=now() - 10**10)
            else:
                raise APIError('Unknown queue action')

    def consume(self, pipe, jid):
        tail = collections.deque(maxlen=24)
        for raw in iter(lambda: pipe.readline(65536), b''):
            line = platforms.redact(raw.decode('utf-8', 'replace').strip(), self.state)[:3000]
            if not line:
                continue
            live = self.live.setdefault(jid, {})
            if line.startswith('ZEDITEM '):
                self.write('UPDATE jobs SET items=items+1 WHERE id=?',(jid,))
                live['detail']='Profile media saved to staging; continuing…'
            elif line.startswith('ZEDPROFILE '):
                live['detail']=line[11:][:250]
                live['log']=live['detail']
            elif line.startswith('ZEDPROG '):
                try:
                    info = json.loads(line[8:])
                    done = info.get('downloaded_bytes') or 0
                    total = info.get('total_bytes') or info.get('total_bytes_estimate') or 0
                    live.update(progress=min(99.9, done / total * 100) if total else 0,
                                speed=info.get('speed') or 0, eta=info.get('eta'), bytes=done,
                                detail='Merging / finishing' if info.get('status') == 'finished' else 'Downloading')
                except (ValueError, TypeError):
                    pass
            elif line.startswith('ZEDTITLE '):
                live['name'] = line[9:][:240]
            else:
                tail.append(line)
                live['log'] = '\n'.join(tail)[-8000:]
                if 'warning' in line.lower():
                    live['warning'] = line[:400]
                live['detail'] = line[-250:]
        pipe.close()

    def run_job(self, job):
        jid = job['id']
        instagram_session = None
        try:
            with self.lock:
                if self.job(jid)['status'] not in ('queued', 'retry', 'blocked'):
                    return
                self.update_job(jid, status='running', attempts=job['attempts']+1, detail='Connecting…')
            validate_url(job['url'])
            relative = '/'.join(p for p in (job['folder'], job['subdir']) if p)
            output = self.destination(relative, create=True)
            dest = self.staging(jid) if job['layout']==2 else output
            if job['phase']=='publish' and job['layout']==2:
                publish(self, job, dest, output)
                self.update_job(jid,status='done',progress=100,detail='Saved to '+relative)
                self.clean_metadata(dest)
                return
            key = platforms.platform_for(job['url'])
            prefs = self.platform_settings()[key]
            if key=='instagram' and prefs['require_cookies']:
                cookie = self.cookie_for(job)
                slot = job['cookie_profile'] if job['cookie_profile']!='auto' else key
                if not cookie or not platforms.cookie_status(self.state,slot)['usable']:
                    raise APIError('Instagram login required: add a valid sessionid in Settings → Platforms → Instagram')
            settings = self.settings()
            settings['platforms'] = self.platform_settings()
            cookie = self.cookie_for(job)
            if key in ('instagram','facebook') and cookie:
                instagram_session = platforms.CookieSession(cookie,key)
                cookie = instagram_session.path
            args = build_command(job, dest, cookie, settings, self.state)
            env = dict(os.environ, TMPDIR=str(dest), XDG_CACHE_HOME=str(dest), PYTHONUNBUFFERED='1')
            self.current = jid
            self.live[jid] = {}
            with self.lock:
                # Recheck after preparation so a concurrent pause is respected.
                if self.job(jid)['status'] != 'running':
                    return
                self.process = subprocess.Popen(args, cwd=dest, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, start_new_session=True)
            reader = threading.Thread(target=self.consume, args=(self.process.stdout, jid), daemon=True)
            reader.start()
            while self.process.poll() is None:
                if self.stop.wait(1):
                    self.stop_process()
                    self.update_job(jid, status='queued', detail='Waiting for service restart')
                    return
                fresh = self.job(jid)
                if fresh['status'] != 'running':
                    self.stop_process()
                    return
                disk = self.storage()
                if self.get_setting('hold', False) or not disk['ready']:
                    self.stop_process()
                    self.update_job(jid, status='blocked', detail='Queue held' if self.get_setting('hold', False) else disk['reason'])
                    return
            reader.join(timeout=3)
            if self.job(jid)['status'] != 'running':
                return
            live = self.live.get(jid, {})
            if self.process.returncode == 0:
                if instagram_session:
                    try:
                        instagram_session.commit()
                    except OSError:
                        live['log'] = (live.get('log','')+'\nSession refresh could not be saved; the previously stored session was kept.')[-8000:]
                if job['layout']==2:
                    self.update_job(jid,phase='publish',name=live.get('name',job['name']))
                    publish(self, job, dest, output)
                self.update_job(jid, status='done', progress=100, detail='Saved to ' + relative,
                    name=live.get('name', job['name']), last_error=live.get('log', '')[-8000:])
                if job['layout']==2:
                    self.clean_metadata(dest)
            else:
                if job.get('profile_mode') and job['layout']==2:
                    # Keep the original authentication/rate-limit error if nothing was downloaded.
                    if any(p.is_file() and p.suffix.lower() in MEDIA for p in dest.iterdir()) or self.rows('SELECT source FROM published WHERE job=?',(jid,)):
                        publish(self,job,dest,output)
                raise APIError(live.get('log', '')[-8000:] or f'Downloader exited with code {self.process.returncode}')
        except Exception as exc:
            fresh = self.job(jid)
            if fresh['status'] not in ('paused', 'cancelled'):
                attempts = max(fresh['attempts'], job['attempts'] + 1)
                error = platforms.redact(str(exc), self.state)[-8000:]
                category, detail = platforms.failure_kind(error)
                again = category=='transient' and attempts <= self.get_setting('retry_count', 3)
                self.update_job(jid, status='retry' if again else 'error', attempts=attempts,
                    next_try=now() + min(1800, 60 * 2**min(attempts-1, 5)),
                    last_error=error, error_kind=category, detail=detail if category!='transient' or again else 'Needs attention')
        finally:
            if instagram_session:
                instagram_session.close()
            self.current = None
            self.process = None
            self.live.pop(jid, None)
            (self.state / ('gallery-' + jid + '.json')).unlink(missing_ok=True)
            (self.state / ('profile-' + jid + '.json')).unlink(missing_ok=True)

    def media_loop(self):
        while not self.stop.wait(1):
            try:
                if self.get_setting('hold', False) or not self.storage()['ready']:
                    continue
                items = self.rows("SELECT * FROM jobs WHERE status IN ('queued','retry','blocked') AND next_try<=? ORDER BY position LIMIT 1", (now(),))
                if items:
                    self.run_job(items[0])
            except Exception as exc:
                print('Queue monitor:', type(exc).__name__, flush=True)

    def apply_torrent_settings(self):
        s = self.settings()
        return self.rpc.call('session-set', {
            'download-queue-enabled': True, 'download-queue-size': 1,
            'seed-queue-enabled': True, 'seed-queue-size': 1, 'queue-stalled-enabled': False,
            'speed-limit-down-enabled': bool(s['torrent_down_kib']), 'speed-limit-down': s['torrent_down_kib'],
            'speed-limit-up-enabled': True, 'speed-limit-up': max(1, s['torrent_up_kib']),
            'seedRatioLimited': True, 'seedRatioLimit': s['seed_ratio'],
            'peer-limit-global': 40, 'peer-limit-per-torrent': 20,
            'cache-size-mb': 16, 'port-forwarding-enabled': False,
        })

    def owned_torrent(self, torrent):
        try:
            return Path(torrent['downloadDir']).resolve().is_relative_to(self.root.resolve())
        except (KeyError, OSError):
            return False

    def torrent_loop(self):
        configured = False
        while not self.stop.is_set():
            try:
                if not configured:
                    self.apply_torrent_settings()
                    configured = True
                result = self.rpc.call('torrent-get', {'fields': ['id','hashString','name','status','percentDone',
                    'rateDownload','rateUpload','eta','totalSize','errorString','uploadRatio','downloadDir','metadataPercentComplete','queuePosition']})
                items = [t for t in result.get('torrents', []) if self.owned_torrent(t)]
                with self.lock:
                    self.torrents = items
                    self.torrent_error = ''
                active = [t['hashString'] for t in items if t['status'] != 0]
                blocked = self.get_setting('blocked_torrents', [])
                if self.get_setting('hold', False) or not self.storage()['ready']:
                    if active:
                        self.set_setting('blocked_torrents', list(set(blocked + active)))
                        self.rpc.call('torrent-stop', {'ids': active})
                elif blocked:
                    ids = [t['hashString'] for t in items if t['hashString'] in blocked]
                    if ids:
                        self.rpc.call('torrent-start', {'ids': ids})
                    self.set_setting('blocked_torrents', [])
            except Exception as exc:
                self.torrent_error = str(exc)
                configured = False
            self.stop.wait(4)

    def start(self):
        for function in (self.media_loop, self.torrent_loop):
            thread = threading.Thread(target=function, daemon=True)
            thread.start()
            self.threads.append(thread)

    def close(self):
        self.speedtest.close()
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=12)
        self.stop_process()
        self.db.close()

    def snapshot(self):
        jobs = self.rows("SELECT * FROM jobs ORDER BY CASE WHEN status='running' THEN 0 WHEN status IN ('queued','retry','blocked','paused') THEN 1 ELSE 2 END, CASE WHEN status IN ('queued','retry','blocked','paused') THEN position ELSE -created END LIMIT 1000")
        for job in jobs:
            job.update(self.live.get(job['id'], {}))
            job.pop('last_error', None)
        return {'storage': self.storage(), 'jobs': jobs, 'torrents': self.torrents,
                'torrent_error': self.torrent_error, 'settings': self.settings(), 'speedtest': self.speedtest.snapshot(),
                'cookies': {s: (self.state / ('cookies-' + s + '.txt')).exists() for s in COOKIE_SLOTS},
                'platforms': self.platform_settings(), 'cookie_status': {s: platforms.cookie_status(self.state,s) for s in COOKIE_SLOTS},
                'uptime': int(now()-self.started), 'version': '1.3.0', 'tools': {'facebook_browser': profiles.browser_ready(), 'local_url': 'http://zed.local', 'local_enabled': Path('/etc/zedair/local-address-enabled').exists()}, 'session_days': DAYS}

    def get_session(self, raw):
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(raw or '')
            token = jar['zedair_session'].value
        except (KeyError, http.cookies.CookieError):
            return None
        found = self.rows('SELECT * FROM sessions WHERE token=? AND expires>?', (digest(token), now()))
        return found[0] if found else None

    def new_session(self):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.write('DELETE FROM sessions WHERE expires<?', (now(),))
        self.write('INSERT INTO sessions VALUES (?,?,?)', (digest(token), csrf, now()+DAYS*86400))
        # Bound active sessions without logging out ordinary household use.
        self.write('DELETE FROM sessions WHERE token NOT IN (SELECT token FROM sessions ORDER BY expires DESC LIMIT 30)')
        return token, csrf

    def throttle(self, ip):
        with self.lock:
            q = self.failures[ip]
            while q and q[0] < now()-900:
                q.popleft()
            if len(q) >= 10:
                raise APIError('Too many login attempts. Try again in 15 minutes.', 429)
            q.append(now())
            if len(self.failures) > 1000:
                self.failures.clear()


class Handler(BaseHTTPRequestHandler):
    server_version = 'Zedair'
    protocol_version = 'HTTP/1.1'

    @property
    def desk(self):
        return self.server.desk

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt, *args):
        pass  # Do not put URLs or browser cookies into the system journal.

    def headers_common(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def reply(self, data, status=200, cookie=None):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.headers_common()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        if cookie is not None:
            flag = '; Secure' if self.desk.config.get('tls_cert') else ''
            self.send_header('Set-Cookie', f'zedair_session={cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age={DAYS*86400 if cookie else 0}{flag}')
        self.end_headers()
        if self.command!='HEAD':
            self.wfile.write(raw)

    def read_json(self):
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            raise APIError('JSON requests are required', 415)
        length = int(self.headers.get('Content-Length', '0'))
        if length <= 0 or length > 12*1024*1024:
            self.close_connection = True
            raise APIError('Request is empty or too large', 413)
        try:
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise APIError('Invalid JSON')

    def require_session(self, mutation=False):
        session = self.desk.get_session(self.headers.get('Cookie'))
        if not session:
            raise APIError('Please sign in', 401)
        if mutation and not hmac.compare_digest(self.headers.get('X-Zedair-CSRF', ''), session['csrf']):
            raise APIError('Refresh this page before making changes', 403)
        return session

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self.get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (APIError, ValueError, OSError) as exc:
            self.reply({'error': str(exc)}, getattr(exc, 'status', 400))
        except Exception:
            self.reply({'error': 'Unexpected server error; inspect the service journal'}, 500)

    def get(self):
        p = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(p.query)
        d = self.desk
        if p.path in ('/', '/app.js', '/style.css', '/favicon.svg'):
            file = HERE / 'static' / ('index.html' if p.path == '/' else p.path[1:])
            raw = file.read_bytes()
            self.send_response(200)
            self.headers_common()
            self.send_header('Content-Type', (mimetypes.guess_type(str(file))[0] or 'text/plain') + '; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            if self.command!='HEAD':
                self.wfile.write(raw)
            return
        if p.path == '/api/auth':
            session = d.get_session(self.headers.get('Cookie'))
            admin = d.rows('SELECT username FROM admin')
            self.reply({'setup': not bool(admin), 'authenticated': bool(session),
                        'username': admin[0]['username'] if session and admin else '',
                        'csrf': session['csrf'] if session else ''})
            return
        self.require_session()
        if p.path == '/api/state':
            self.reply(d.snapshot())
        elif p.path == '/api/system':
            storage=d.storage(reserve=False)
            self.reply(d.systemstats.snapshot(d.root if storage['ready'] else None))
        elif p.path == '/api/folders':
            relative = query.get('path', [''])[0]
            path = d.destination(relative, reserve=False)
            if not path.is_dir():
                raise APIError('Folder not found', 404)
            try:
                offset=int(query.get('offset',['0'])[0]);limit=int(query.get('limit',['1000'])[0])
                if offset<0 or not 1<=limit<=1000:raise ValueError()
            except ValueError:raise APIError('Invalid file page')
            entries = [];total=0
            reserved = {d.root/r['folder']/r['subdir']/r['target'] for r in d.rows('SELECT p.target,j.folder,j.subdir FROM published p JOIN jobs j ON p.job=j.id WHERE p.complete=0')}
            for item in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if item.is_symlink() or item.name.startswith('.') or item in reserved:continue
                total+=1
                if total<=offset or len(entries)>=limit:continue
                try:
                    stat=item.stat();directory=item.is_dir()
                    entries.append({'name': item.name, 'directory': directory, 'size': stat.st_size if not directory else 0,
                        'preview': '' if directory else preview_type(item.suffix), 'stamp': str(stat.st_size)+':'+str(stat.st_mtime_ns)})
                except FileNotFoundError:continue
            self.reply({'path':relative,'entries':entries,'limit':limit,'offset':offset,'total':total,'more':offset+len(entries)<total})
        elif p.path == '/api/speedtest/history.csv':
            raw=history_csv(d.speedtest.history())
            self.send_response(200);self.headers_common()
            self.send_header('Content-Type','text/csv; charset=utf-8')
            self.send_header('Content-Disposition','attachment; filename="zedair-speed-tests.csv"')
            self.send_header('Content-Length',str(len(raw)));self.end_headers()
            if self.command!='HEAD':self.wfile.write(raw)
        elif p.path in ('/api/file','/api/preview'):
            file = d.destination(query.get('path', [''])[0], reserve=False)
            if not file.is_file():
                raise APIError('File not found', 404)
            inline=p.path=='/api/preview'
            media=PREVIEW_MIME.get(file.suffix.lower())
            if inline and not media:
                raise APIError('This file type cannot be previewed safely. Use Download instead.',415)
            with file.open('rb') as stream:
                size=os.fstat(stream.fileno()).st_size
                try:
                    start,end=parse_range(self.headers.get('Range'),size)
                except ValueError:
                    self.send_response(416);self.headers_common()
                    self.send_header('Content-Range',f'bytes */{size}')
                    self.send_header('Content-Length','0');self.end_headers();return
                ranged=self.headers.get('Range') is not None
                self.send_response(206 if ranged else 200)
                self.headers_common()
                self.send_header('Content-Type',media if inline else 'application/octet-stream')
                disposition='inline' if inline else 'attachment'
                self.send_header('Content-Disposition',disposition+"; filename*=UTF-8''"+urllib.parse.quote(file.name))
                self.send_header('Accept-Ranges','bytes')
                if ranged:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.send_header('Content-Length',str(max(0,end-start+1)))
                self.end_headers()
                if self.command=='HEAD':return
                stream.seek(start)
                remaining=end-start+1
                while remaining>0:
                    block=stream.read(min(256*1024,remaining))
                    if not block:break
                    self.wfile.write(block);remaining-=len(block)
        elif p.path == '/api/job':
            job = d.job(query.get('id', [''])[0])
            job.update(d.live.get(job['id'], {}))
            self.reply(job)
        elif p.path == '/api/torrent':
            tid = query.get('id', [''])[0]
            torrents = d.rpc.call('torrent-get', {'ids': [tid], 'fields': ['hashString', 'name', 'files', 'fileStats', 'downloadDir']}).get('torrents', [])
            if not torrents or not d.owned_torrent(torrents[0]):
                raise APIError('Torrent not found', 404)
            self.reply(torrents[0])
        else:
            raise APIError('Not found', 404)

    def do_POST(self):
        try:
            self.post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (APIError, ValueError, OSError, KeyError, TypeError) as exc:
            self.reply({'error': str(exc)}, getattr(exc, 'status', 400))
        except Exception:
            self.reply({'error': 'Unexpected server error; inspect the service journal'}, 500)

    def post(self):
        d = self.desk
        path = urllib.parse.urlsplit(self.path).path
        origin = self.headers.get('Origin')
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get('Host'):
            self.close_connection = True
            raise APIError('Cross-origin request refused', 403)
        data = self.read_json()
        if path in ('/api/setup', '/api/login'):
            d.throttle(self.client_address[0])
            username, password = data.get('username', ''), data.get('password', '')
            if not isinstance(username, str) or not isinstance(password, str) or len(password) > 512:
                raise APIError('Invalid credentials')
            with d.lock:
                admin = d.rows('SELECT * FROM admin')
                if path.endswith('setup'):
                    if admin:
                        raise APIError('Administrator already exists', 409)
                    supplied = data.get('setup_key', '')
                    if not isinstance(supplied, str) or not hmac.compare_digest(digest(supplied), d.config.get('setup_key_hash', '')):
                        raise APIError('Incorrect installation setup key', 403)
                    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', username) or len(password) < 12:
                        raise APIError('Use a 3–64 character username and a password of at least 12 characters')
                    d.write('INSERT INTO admin VALUES (1,?,?)', (username, password_hash(password)))
                elif not admin or username != admin[0]['username'] or not password_ok(password, admin[0]['password']):
                    raise APIError('Incorrect username or password', 401)
            token, csrf = d.new_session()
            self.reply({'ok': True, 'csrf': csrf}, cookie=token)
            return
        session = self.require_session(mutation=True)
        if path == '/api/logout':
            d.write('DELETE FROM sessions WHERE token=?', (session['token'],))
            self.reply({'ok': True}, cookie='')
            return
        if path == '/api/password':
            admin = d.rows('SELECT * FROM admin')[0]
            if not password_ok(data.get('old', ''), admin['password']):
                raise APIError('Current password is incorrect', 403)
            password = data.get('password', '')
            if not isinstance(password, str) or not 12 <= len(password) <= 512:
                raise APIError('Password must contain 12–512 characters')
            d.write('UPDATE admin SET password=?', (password_hash(password),))
            d.write('DELETE FROM sessions')
            self.reply({'ok': True}, cookie='')
            return
        if path == '/api/profiles':
            self.reply(d.add_profile(data));return
        if path == '/api/jobs':
            self.reply(d.add_jobs(data))
            return
        if path == '/api/profiles/retry':
            job=d.job(data.get('id'));limit=data.get('scroll_limit')
            if not job.get('profile_mode') or job['status'] not in ('paused','error','blocked'):raise APIError('This profile cannot be retried now')
            if type(limit) is not int or not 10<=limit<=10000:raise APIError('Scroll limit must be between 10 and 10,000')
            d.update_job(job['id'],scroll_limit=limit);d.job_action(job['id'],'retry')
        elif path == '/api/job/action':
            d.job_action(data.get('id'), data.get('action'))
        elif path == '/api/history/clear':
            d.write("DELETE FROM jobs WHERE status IN ('done','cancelled')")
        elif path == '/api/folders':
            d.destination(data.get('path', ''), create=True)
        elif path == '/api/hold':
            d.set_setting('hold', bool(data.get('hold')))
        elif path == '/api/settings':
            values = {}
            for key, lo, hi in [('media_kib',0,100000), ('torrent_down_kib',0,100000),
                                ('torrent_up_kib',1,100000), ('reserve_gib',1,100),
                                ('playlist_limit',1,500), ('retry_count',0,8), ('seed_ratio',0,10)]:
                value = float(data[key]) if key == 'seed_ratio' else int(data[key])
                if not lo <= value <= hi:
                    raise APIError(f'{key} must be between {lo} and {hi}')
                values[key] = value
            for key, value in values.items():
                d.set_setting(key, value)
            try:
                d.apply_torrent_settings()
            except APIError:
                pass  # Saved settings are applied when the daemon returns.
        elif path == '/api/speedtest':
            if data.get('action')=='start':
                d.speedtest.start(accept_terms=data.get('accept_terms',False))
            elif data.get('action')=='cancel':
                d.speedtest.cancel()
            else:
                raise APIError('Unknown speed-test action')
        elif path == '/api/file/delete':
            d.delete_file(data)
        elif path == '/api/platforms':
            key = data.get('platform')
            values = platforms.validate_preferences(key,data.get('values'),safe_relative)
            saved = d.platform_settings()
            saved[key] = values
            d.set_setting('platforms',saved)
        elif path == '/api/cookies':
            with d.lock:
                platforms.save_cookies(d.state,data.get('slot'),data)
        elif path == '/api/torrents':
            if d.get_setting('hold', False):
                raise APIError('Resume the queue before adding a torrent', 409)
            folder = d.destination(data.get('folder', ''), create=True)
            arguments = {'download-dir': str(folder), 'paused': bool(data.get('paused')), 'peer-limit': 20}
            if data.get('magnet'):
                magnet = data['magnet']
                if not isinstance(magnet, str) or len(magnet) > 16000 or not magnet.startswith('magnet:?') or 'xt=urn:btih:' not in magnet:
                    raise APIError('Use a valid BitTorrent v1/hybrid magnet link')
                arguments['filename'] = magnet
            else:
                meta = data.get('metainfo', '')
                decoded = base64.b64decode(meta, validate=True)
                if not 1 <= len(decoded) <= 8*1024*1024 or not decoded.startswith(b'd'):
                    raise APIError('Invalid or oversized .torrent file')
                arguments['metainfo'] = meta
            self.reply(d.rpc.call('torrent-add', arguments))
            return
        elif path == '/api/torrent/action':
            tid = data.get('id')
            items = d.rpc.call('torrent-get', {'ids': [tid], 'fields': ['hashString','downloadDir','files']}).get('torrents', [])
            if not items or not d.owned_torrent(items[0]):
                raise APIError('Torrent not found', 404)
            action = data.get('action')
            if action == 'resume':
                if d.get_setting('hold', False) or not d.storage()['ready']:
                    raise APIError('Resume the queue and connect the SSD first', 409)
                d.rpc.call('torrent-start', {'ids': [tid]})
            elif action == 'pause':
                d.rpc.call('torrent-stop', {'ids': [tid]})
                d.set_setting('blocked_torrents', [x for x in d.get_setting('blocked_torrents', []) if x != tid])
            elif action == 'remove':
                d.rpc.call('torrent-remove', {'ids': [tid], 'delete-local-data': False})
                d.set_setting('blocked_torrents', [x for x in d.get_setting('blocked_torrents', []) if x != tid])
            elif action == 'files':
                count = len(items[0].get('files', []))
                wanted = data.get('wanted', [])
                if not count:
                    raise APIError('Metadata is not available yet. Start the torrent briefly, then reopen Files.')
                if not isinstance(wanted, list) or any(type(i) is not int or i < 0 or i >= count for i in wanted):
                    raise APIError('Invalid file selection')
                unwanted = [i for i in range(count) if i not in wanted]
                args = {'ids': [tid]}
                # Empty lists mean ALL in Transmission, so omit them instead.
                if wanted:
                    args['files-wanted'] = wanted
                if unwanted:
                    args['files-unwanted'] = unwanted
                d.rpc.call('torrent-set', args)
            elif action == 'verify':
                if not d.storage()['ready'] or d.get_setting('hold', False):
                    raise APIError('Connect the SSD and resume the queue first', 409)
                d.rpc.call('torrent-verify', {'ids': [tid]})
            else:
                raise APIError('Unknown torrent action')
        else:
            raise APIError('Not found', 404)
        self.reply({'ok': True})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/etc/zedair/config.json')
    parser.add_argument('--port', type=int)
    parser.add_argument('--host')
    parser.add_argument('--strictPort', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--demo', action='store_true', help='Isolated preview; no download workers')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    os.umask(0o077)
    desk = Desk(config)
    server = Server((args.host or config.get('host', '0.0.0.0'), args.port or config.get('port', 8787)), Handler)
    server.desk = desk
    if config.get('tls_cert'):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(config['tls_cert'], config['tls_key'])
        server.socket = context.wrap_socket(server.socket, server_side=True)
    if not args.demo:
        desk.start()
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f'Zedair listening on port {server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        desk.close()


if __name__ == '__main__':
    main()
