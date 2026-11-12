#!/usr/bin/env python3
"""Zedair Download Desk — a small, single-user LAN download server."""
import argparse
import importlib.metadata
import base64
import random
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
import queue
import datetime
import engine_settings
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from media import build_command, engine_for, validate_url, normalize_url, url_key
import platforms
import profiles
from systemstats import SystemStats
from publishing import publish, MEDIA
from speedcheck import SpeedTest, history_csv

HERE = Path(__file__).resolve().parent
DAYS = 35
DEFAULTS = {'media_kib': 0, 'torrent_down_kib': 0, 'torrent_up_kib': 256,
            'seed_ratio': 0.0, 'reserve_gib': 5, 'playlist_limit': 100, 'retry_count': 3,
            'hold': False, **engine_settings.DEFAULTS}
QUALITY = ('best', '4320', '2160', '1080', '720', '480', 'audio')
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
        for key, definition in {'container':"TEXT DEFAULT 'auto'", 'layout':'INTEGER DEFAULT 1', 'phase':"TEXT DEFAULT 'download'", 'error_kind':"TEXT DEFAULT ''", 'profile_mode':"TEXT DEFAULT ''", 'profile_username':"TEXT DEFAULT ''", 'scroll_limit':'INTEGER DEFAULT 2000', 'items':'INTEGER DEFAULT 0', 'filename':"TEXT DEFAULT ''", 'images_only':'INTEGER DEFAULT 0', 'browser_fallback':'INTEGER DEFAULT 1', 'engine':"TEXT DEFAULT 'video'", 'browser_force':'INTEGER DEFAULT 0', 'remove_requested':'INTEGER DEFAULT 0'}.items():
            if key not in columns:
                self.db.execute('ALTER TABLE jobs ADD COLUMN '+key+' '+definition)
        self.db.execute('CREATE TABLE IF NOT EXISTS published(job TEXT, source TEXT, target TEXT, signature TEXT, complete INTEGER, PRIMARY KEY(job,source))')
        self.db.execute('CREATE TABLE IF NOT EXISTS file_dates(folder TEXT,name TEXT,size INTEGER,mtime_ns INTEGER,downloaded REAL,PRIMARY KEY(folder,name))')
        if not self.db.execute("SELECT 1 FROM settings WHERE key='instagram_download_fix_v131'").fetchone():
            # v1.3's print-only profile command produced false counts and could leave
            # an empty job stuck in publication. Do not touch direct URLs or files.
            self.db.execute("""UPDATE jobs SET items=0,phase='download',progress=0,
                detail='Profile download fix applied. Resume/retry this job to download its media.'
                WHERE kind='instagram' AND profile_mode!='' AND NOT EXISTS
                (SELECT 1 FROM published p WHERE p.job=jobs.id AND p.complete=1)""")
            self.db.execute("INSERT INTO settings(key,value) VALUES('instagram_download_fix_v131','true')")
        self.db.execute("UPDATE jobs SET status='queued', detail='Recovering after service restart' WHERE status='running'")
        self.db.execute('DELETE FROM published WHERE job IN (SELECT id FROM jobs WHERE remove_requested=1)')
        self.db.execute('DELETE FROM jobs WHERE remove_requested=1')
        self.db.execute('CREATE TABLE IF NOT EXISTS usage(day TEXT,domain TEXT,bytes INTEGER DEFAULT 0,PRIMARY KEY(day,domain))')
        self.db.commit()
        if self.get_setting('telegram_errors') is None:
            # Claude enabled error notices through a configured token/recipient;
            # retain that choice while giving new installations an explicit switch.
            enabled=bool(self.get_setting('telegram_bot_token','') and (self.get_setting('telegram_chat_id','') or self.get_setting('telegram_admin_id','')))
            self.set_setting('telegram_errors',enabled)
        self.engine_versions={}
        for package in ('yt-dlp','gallery-dl','playwright'):
            try:self.engine_versions[package]=importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:self.engine_versions[package]='Not installed'
        self.file_lock = threading.RLock()
        self.stop = threading.Event()
        self.current = None
        self.process = None
        self.processes = {}
        self.workers = {}
        self.notices = queue.Queue(maxsize=20)
        self.usage_pending = {}
        self.usage_flushed = now()
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
        values={k: self.get_setting(k, v) for k, v in DEFAULTS.items()}
        values['simultaneous_downloads']=max(1,min(4,int(values['simultaneous_downloads'])))
        return values

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
        with self.file_lock, self.lock:
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
            self.write('DELETE FROM file_dates WHERE folder=? AND name=?',('' if path.parent==self.root else path.parent.relative_to(self.root).as_posix(),path.name))

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
        if not isinstance(urls, list) or not 1 <= len(urls) <= 100:
            raise APIError('Paste between 1 and 100 URLs')
        quality = data.get('quality', 'best')
        if quality not in (*QUALITY, 'platform'):
            raise APIError('Unknown quality preset')
        container = data.get('container','auto')
        if container not in ('platform','auto','mp4'):
            raise APIError('Choose automatic format or MP4 only')
        cookie = data.get('cookie_profile', 'auto')
        if cookie not in (*COOKIE_SLOTS, 'auto', 'none'):
            raise APIError('Unknown cookie profile')
        folder = safe_relative(data.get('folder', ''))
        name = safe_relative(data.get('name', ''))
        custom = safe_relative(data.get('filename', '').strip())
        if '/' in custom or len(custom.encode())>110:raise APIError('Use a filename of at most 110 bytes, without folders')
        if '/' in name or len(name) > 80:
            raise APIError('Use one short display label')
        # Validate the whole batch before recording any work. Queuing while unplugged is allowed.
        clean = []
        for url in urls:
            if not isinstance(url,str):raise APIError('Each download link must be text')
            url = normalize_url(url.strip())
            validate_url(url, dns=False)
            kind = engine_for(url)
            if kind == 'instagram' and not re.search(r'/(p|reel|reels|tv)/[^/]+', urllib.parse.urlsplit(url).path):
                raise APIError('Instagram requires a direct post, carousel or reel URL')
            if kind == 'threads' and not re.search(r'/(post|t|share)/[\w-]+', urllib.parse.urlsplit(url).path):
                raise APIError('Threads requires a direct post URL')
            if not self.platform_settings()[platforms.platform_for(url)]['enabled']:
                raise APIError('This platform is disabled in Settings')
            safe_relative(platforms.platform_folder(url,self.platform_settings()))
            clean.append((url, kind))
        result = []
        with self.lock:
            existing={url_key(r['url']) for r in self.rows("SELECT url FROM jobs WHERE remove_requested=0 AND status!='cancelled'")}
            pending=[]
            for url,kind in clean:
                key=url_key(url)
                if key in existing:continue
                existing.add(key);pending.append((url,kind))
            if len(self.rows('SELECT id FROM jobs WHERE remove_requested=0'))+len(pending)>1000:
                raise APIError('History is full (1,000 jobs). Clear finished history first.')
            for url,kind in pending:
                jid = secrets.token_hex(6)
                prefs = self.platform_settings()[platforms.platform_for(url)]
                subdir = '' if folder or data.get('folder_selected') is True else platforms.platform_folder(url,self.platform_settings())
                selected_quality = prefs.get('quality','best') if quality=='platform' else quality
                self.db.execute('''INSERT INTO jobs(id,url,kind,folder,name,subdir,quality,playlist,subtitles,cookie_profile,status,created,updated,position,layout)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,2)''',
                    (jid, url, kind, folder, name or urllib.parse.urlsplit(url).hostname, subdir, selected_quality,
                     int(bool(data.get('playlist') or (quality=='platform' and prefs.get('playlist')))), int(bool(data.get('subtitles') or (quality=='platform' and prefs.get('subtitles')))), cookie, 'queued', now(), now(), now()))
                selected_container=prefs.get('container','auto') if container=='platform' else container
                self.db.execute('UPDATE jobs SET container=?,filename=?,browser_fallback=? WHERE id=?',(selected_container,custom,int(data.get('browser_fallback',True) is True),jid))
                result.append(jid)
            self.db.commit()
        return {'ids': result, 'skipped': len(urls)-len(result)}

    def add_profile(self, data):
        platform=data.get('platform')
        username,url=profiles.profile_target(platform,data.get('profile',''))
        mode=data.get('mode','everything')
        if mode=='images':
            if platform!='instagram':raise APIError('Images only is available for Instagram')
            mode='everything';data={**data,'images_only':True}
        if mode not in (*profiles.MODES,'avatar') or (mode=='avatar' and platform!='instagram'):raise APIError('Choose a supported profile mode')
        images_only=data.get('images_only',False) is True
        if images_only and (platform!='instagram' or mode=='reels'):raise APIError('Images only is available for Instagram posts or everything')
        limit=data.get('scroll_limit',2000)
        if type(limit) is not int or not 10<=limit<=10000:raise APIError('Scroll limit must be between 10 and 10,000')
        if not self.platform_settings()[platform]['enabled']:raise APIError('Enable this platform in Settings first')
        if not platforms.cookie_status(self.state,platform)['usable']:raise APIError('Add a usable '+platform+' session in Settings → Platforms first')
        if platform=='facebook' and not profiles.browser_ready():raise APIError('Install the optional collector on the laptop: sudo bash /opt/zedair/install-facebook.sh')
        folder=safe_relative(data.get('folder',''))
        subdir='' if folder else safe_relative(('Instagram' if platform=='instagram' else 'Facebook')+'/'+username)
        with self.lock:
            if self.db.execute("SELECT id FROM jobs WHERE url=? AND profile_mode=? AND images_only=? AND remove_requested=0 AND status IN ('queued','running','retry','paused','blocked')",(url,mode,int(images_only))).fetchone():
                raise APIError('This profile and selection are already in the queue')
            if self.db.execute('SELECT count(*) FROM jobs').fetchone()[0]>=1000:raise APIError('History is full. Clear finished jobs first.')
            jid=secrets.token_hex(6)
            self.db.execute("""INSERT INTO jobs(id,url,kind,folder,name,subdir,quality,playlist,subtitles,cookie_profile,status,created,updated,position,layout,profile_mode,profile_username,scroll_limit)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,2,?,?,?)""",(jid,url,engine_for(url),folder,platform.title()+' / '+username,subdir,'best',0,0,platform,'queued',now(),now(),now(),mode,username,limit))
            self.db.execute('UPDATE jobs SET images_only=? WHERE id=?',(int(images_only),jid))
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
                if item.is_file() and not item.is_symlink() and not item.name.startswith('.'):
                    item.unlink()
            if not any(stage.iterdir()):
                stage.rmdir()
        except OSError:
            pass  # Publication has completed; optional metadata cleanup must not change its status.

    def stop_process(self, jid=None):
        with self.lock:
            targets=[self.processes[jid]] if jid in self.processes else [] if jid else list(self.processes.values())
            if jid is None and self.process and self.process not in targets:targets.append(self.process)
        for process in targets:
            if process.poll() is not None:continue
            try:
                os.killpg(process.pid,signal.SIGTERM)
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
                except ProcessLookupError:pass
            except ProcessLookupError:pass

    def job_action(self, jid, action):
        with self.lock:
            job = self.job(jid)
            if action=='remove':
                self.update_job(jid,status='cancelled',remove_requested=1,detail='Removed from queue')
                if jid not in self.workers and jid not in self.processes and self.current!=jid:
                    self.db.execute('DELETE FROM published WHERE job=?',(jid,))
                    self.db.execute('DELETE FROM jobs WHERE id=?',(jid,));self.db.commit()
                return
            if action in ('browser','refresh'):
                if job['status']=='running':raise APIError('Pause the download before changing its engine')
                if job['kind'] not in ('video','threads') or job['profile_mode']:raise APIError('Browser retry is for individual video pages')
                self.update_job(jid,browser_fallback=1,browser_force=1)
                action='retry'
            if action in ('pause', 'cancel'):
                self.update_job(jid, status='paused' if action == 'pause' else 'cancelled', detail='Paused' if action == 'pause' else 'Cancelled; partial files kept')
                # The worker notices the new state and stops its complete process group.
            elif action in ('resume', 'retry'):
                if job['status']=='done':raise APIError('This download is complete. Remove its queue entry before adding the URL again.')
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
            live['activity']=time.monotonic()
            if line.startswith('ZEDENGINE '):
                engine=line[10:].strip()
                if engine in ('video','page','browser','direct','gallery'):
                    live['engine']=engine;self.update_job(jid,engine=engine)
            elif line.startswith('ZEDOWNER '):
                owner=line[9:].strip()
                if re.fullmatch(r'[A-Za-z0-9_.]{1,30}',owner):live['owner']=owner
            elif line.startswith('ZEDBYTES '):
                try:
                    live['exact_bytes']=True;self.record_usage(jid,int(line[9:]))
                except ValueError:pass
            elif line.startswith('ZEDITEM '):
                self.write('UPDATE jobs SET items=items+1 WHERE id=?',(jid,))
                live['detail']='Profile media saved to staging; continuing…'
            elif line.startswith('ZEDPROFILE '):
                live['detail']=line[11:][:250]
                tail.append(live['detail']);live['log']='\n'.join(tail)[-8000:]
            elif line.startswith('ZEDPROG '):
                try:
                    info = json.loads(line[8:])
                    done = info.get('downloaded_bytes') or 0
                    total = info.get('total_bytes') or info.get('total_bytes_estimate') or 0
                    if not live.get('exact_bytes'):
                        old=live.get('counted_bytes',0) if live.get('counted_file')==info.get('filename') else 0
                        self.record_usage(jid,max(0,done-old));live['counted_bytes']=done;live['counted_file']=info.get('filename')
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
        process = None
        try:
            with self.lock:
                if self.job(jid)['status'] not in ('queued', 'retry', 'blocked'):
                    return
                self.current=jid
                self.update_job(jid, status='running', attempts=job['attempts']+1, engine='video', detail='Connecting…')
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
            if job['cookie_profile']=='none':settings['auto_cookies']=False
            settings['platforms'] = self.platform_settings()
            cookie = self.cookie_for(job)
            if key in ('instagram','facebook') and cookie:
                instagram_session = platforms.CookieSession(cookie,key)
                cookie = instagram_session.path
            command_job={**job,'kind':'video'} if job['kind']=='threads' else job
            args = build_command(command_job, dest, cookie, settings, self.state) if job.get('profile_mode') not in ('stories','highlights') else []
            if job.get('profile_mode') in ('stories','highlights'):
                spec=self.state/('profile-'+jid+'.json')
                spec.write_text(json.dumps({'url':job['url'],'dest':str(dest),'cookie':str(cookie or ''),'settings':settings}));spec.chmod(0o600)
                args=[sys.executable,str(HERE/'instagram_story.py'),str(spec)]
            if job['kind'] in ('video','threads') and not job.get('profile_mode') and 'yt_dlp' in args:
                spec=self.state/('video-'+jid+'.json')
                spec.write_text(json.dumps({'args':args,'url':job['url'],'dest':str(dest),'state':str(self.state),'cookie':str(cookie or ''),'enabled':bool(job.get('browser_fallback',1)),'force':bool(job.get('browser_force')),'quality':job['quality'],'container':job.get('container','auto'),'filename':job.get('filename',''),'settings':settings}))
                spec.chmod(0o600)
                args=[sys.executable,str(Path(__file__).with_name('download_engine.py')),str(spec)]
            env = dict(os.environ, TMPDIR=str(dest), XDG_CACHE_HOME=str(dest), PYTHONUNBUFFERED='1')
            self.current = jid
            self.live[jid] = {}
            with self.lock:
                # Recheck after preparation so a concurrent pause is respected.
                if self.job(jid)['status'] != 'running':
                    return
                process = subprocess.Popen(args, cwd=dest, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, start_new_session=True)
                self.processes[jid]=process;self.process=process
            reader = threading.Thread(target=self.consume, args=(process.stdout, jid), daemon=True)
            reader.start()
            started=time.monotonic()
            while process.poll() is None:
                if self.stop.wait(1):
                    self.stop_process(jid)
                    self.update_job(jid, status='queued', detail='Waiting for service restart')
                    return
                fresh = self.job(jid)
                if fresh['status'] != 'running':
                    self.stop_process(jid)
                    return
                if time.monotonic()-self.live.get(jid,{}).get('activity',started)>900:
                    self.stop_process(jid);raise APIError('Worker produced no output for 15 minutes; process stopped and partial files kept')
                disk = self.storage()
                if self.get_setting('hold', False) or not disk['ready']:
                    self.stop_process(jid)
                    self.update_job(jid, status='blocked', detail='Queue held' if self.get_setting('hold', False) else disk['reason'])
                    return
            reader.join(timeout=3)
            if self.job(jid)['status'] != 'running':
                return
            live = self.live.get(jid, {})
            if process.returncode == 79 and job.get('images_only') and key=='instagram':
                self.update_job(jid,status='done',progress=100,detail='Available feed examined; no matching images were exposed by Instagram.')
                return
            if process.returncode == 75 and job.get('profile_mode') and key=='instagram':
                if instagram_session:instagram_session.commit()
                if any(p.is_file() and p.suffix.lower() in MEDIA for p in dest.iterdir()):publish(self,job,dest,output)
                self.update_job(jid,status='retry',next_try=now()+random.randint(120,300),position=now(),
                    detail='Page checkpoint saved. Short rest before continuing; other downloads can run.',last_error=live.get('log',''))
                return
            if job.get('profile_mode') in ('stories','highlights') and not job['folder']:
                owner=live.get('owner') or job.get('profile_username')
                if owner:
                    relative='Instagram/'+safe_relative(owner)
                    output=self.destination(relative,create=True)
                    self.update_job(jid,subdir=relative,profile_username=owner);job={**job,'subdir':relative}
                elif process.returncode==0:
                    raise APIError('Instagram did not identify the story owner; media kept in staging. Choose a custom folder to retry.')
            if process.returncode == 0:
                if instagram_session:
                    try:
                        instagram_session.commit()
                    except OSError:
                        live['log'] = (live.get('log','')+'\nSession refresh could not be saved; the previously stored session was kept.')[-8000:]
                if job['layout']==2:
                    self.update_job(jid,phase='publish',name=live.get('name',job['name']))
                    publish(self, job, dest, output)
                self.update_job(jid, status='done', progress=100, detail=('Available profile feed finished; saved to ' if job.get('profile_mode') else 'Saved to ') + relative,
                    name=live.get('name', job['name']), last_error=live.get('log', '')[-8000:])
                if job['layout']==2:
                    self.clean_metadata(dest)
            else:
                if job.get('profile_mode') and job['layout']==2:
                    # Keep the original authentication/rate-limit error if nothing was downloaded.
                    if any(p.is_file() and p.suffix.lower() in MEDIA for p in dest.iterdir()) or self.rows('SELECT source FROM published WHERE job=?',(jid,)):
                        publish(self,job,dest,output)
                raise APIError(live.get('log', '')[-8000:] or f'Downloader exited with code {process.returncode}')
        except Exception as exc:
            fresh = self.job(jid)
            if fresh['status'] not in ('paused', 'cancelled'):
                attempts = max(fresh['attempts'], job['attempts'] + 1)
                error = platforms.redact(str(exc), self.state)[-8000:]
                category, detail = platforms.failure_kind(error)
                self.queue_notice(job,error)
                profile_retry=job.get('profile_mode') in ('posts','reels','everything','avatar') and job['kind']=='instagram' and category in ('transient','rate_limit','access') and attempts<=96
                again = profile_retry or (category=='transient' and attempts <= self.get_setting('retry_count', 3))
                delay=random.randint(1800,3600)*min(8,2**min(attempts-1,3)) if profile_retry else min(1800,60*2**min(attempts-1,5))
                if profile_retry:detail='Collection incomplete. Saved progress kept; cooling down before automatic retry.'
                self.update_job(jid, status='retry' if again else 'error', attempts=attempts,
                    next_try=now() + delay,
                    last_error=error, error_kind=category, detail=detail if category!='transient' or again else 'Needs attention')
        finally:
            if instagram_session:
                instagram_session.close()
            with self.lock:
                removed=self.rows('SELECT remove_requested FROM jobs WHERE id=?',(jid,))
                if removed and removed[0]['remove_requested']:
                    self.db.execute('DELETE FROM published WHERE job=?',(jid,))
                    self.db.execute('DELETE FROM jobs WHERE id=?',(jid,));self.db.commit()
                self.processes.pop(jid,None);self.workers.pop(jid,None)
                if self.current==jid:self.current=next(iter(self.processes),None)
                if self.process is process:self.process=next(iter(self.processes.values()),None)
            self.flush_usage(force=True)
            self.live.pop(jid, None)
            (self.state / ('video-' + jid + '.json')).unlink(missing_ok=True)
            (self.state / ('gallery-' + jid + '.json')).unlink(missing_ok=True)
            (self.state / ('profile-' + jid + '.json')).unlink(missing_ok=True)

    def media_loop(self):
        while not self.stop.wait(1):
            try:
                if self.get_setting('hold',False) or not self.storage()['ready']:continue
                with self.lock:
                    limit=max(1,min(4,int(self.get_setting('simultaneous_downloads',2))))
                    available=limit-len(self.workers)
                    if available<=0:continue
                    rows=self.rows("SELECT * FROM jobs WHERE status IN ('queued','retry','blocked') AND next_try<=? ORDER BY position",(now(),))
                    for row in rows:
                        if available<=0:break
                        if row['id'] in self.workers:continue
                        # Account-bound collections never overlap with another job using the same platform.
                        key=platforms.platform_for(row['url'])
                        running=[self.job(jid) for jid in self.workers]
                        if key in ('instagram','facebook') and any(platforms.platform_for(r['url'])==key for r in running):continue
                        thread=threading.Thread(target=self.run_job,args=(row,),daemon=True)
                        self.workers[row['id']]=thread  # reserve BEFORE starting/preparation
                        thread.start();available-=1
            except Exception as exc:
                print('Queue monitor:',type(exc).__name__,flush=True)

    def apply_torrent_settings(self):
        s = self.settings()
        return self.rpc.call('session-set', {
            'download-queue-enabled': True, 'download-queue-size': min(4,s['simultaneous_downloads']),
            'seed-queue-enabled': True, 'seed-queue-size': 1, 'queue-stalled-enabled': False,
            'speed-limit-down-enabled': bool(s['torrent_down_kib']), 'speed-limit-down': s['torrent_down_kib'],
            'speed-limit-up-enabled': True, 'speed-limit-up': max(1, s['torrent_up_kib']),
            'seedRatioLimited': True, 'seedRatioLimit': s['seed_ratio'],
            'peer-limit-global': 40, 'peer-limit-per-torrent': 20,
            'cache-size-mb': 16, 'port-forwarding-enabled': False, 'preallocation': 0,
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
        for function in (self.media_loop, self.torrent_loop,self.notification_loop):
            thread = threading.Thread(target=function, daemon=True)
            thread.start()
            self.threads.append(thread)

    def close(self):
        self.speedtest.close()
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=12)
        self.stop_process()
        for thread in list(self.workers.values()):thread.join(timeout=15)
        self.flush_usage(force=True)
        self.db.close()

    def snapshot(self):
        jobs = self.rows("SELECT * FROM jobs WHERE remove_requested=0 ORDER BY CASE WHEN status='running' THEN 0 WHEN status IN ('queued','retry','blocked','paused') THEN 1 ELSE 2 END, CASE WHEN status IN ('queued','retry','blocked','paused') THEN position ELSE -created END LIMIT 1000")
        for job in jobs:
            job.update(self.live.get(job['id'], {}))
            job.pop('last_error', None)
        return {'storage': self.storage(), 'jobs': jobs, 'torrents': self.torrents,
                'torrent_error': self.torrent_error, 'settings': {**self.settings(),'telegram_bot_token':'','telegram_token_set':bool(self.get_setting('telegram_bot_token',''))}, 'speedtest': self.speedtest.snapshot(),
                'cookies': {s: (self.state / ('cookies-' + s + '.txt')).exists() for s in COOKIE_SLOTS},
                'platforms': self.platform_settings(), 'cookie_status': {s: platforms.cookie_status(self.state,s) for s in COOKIE_SLOTS},
                'engine_versions':self.engine_versions,'uptime': int(now()-self.started), 'version': '2.0.0', 'tools': {'facebook_browser': profiles.browser_ready(), 'local_url': 'http://zed.local', 'local_enabled': Path('/etc/zedair/local-address-enabled').exists()}, 'session_days': DAYS}

    def add_stories(self,data):
        from instagram_story import target
        if not self.platform_settings()['instagram']['enabled']:raise APIError('Enable Instagram in Settings first')
        mode=data.get('mode','stories');owner,url=target(data.get('profile',''),mode)
        if not platforms.cookie_status(self.state,'instagram')['usable']:raise APIError('Add a usable Instagram session in Settings first')
        folder=safe_relative(data.get('folder',''))
        with self.lock:
            if self.rows("SELECT id FROM jobs WHERE url=? AND status IN ('queued','running','paused','retry','blocked')",(url,)):raise APIError('This story/highlight collection is already queued')
            if len(self.rows('SELECT id FROM jobs'))>=1000:raise APIError('History is full; clear finished history')
            jid=secrets.token_hex(6);subdir='' if folder else ('Instagram/'+owner if owner else 'Instagram')
            self.db.execute("""INSERT INTO jobs(id,url,kind,folder,name,subdir,quality,playlist,subtitles,cookie_profile,status,created,updated,position,layout,profile_mode,profile_username)
                VALUES(?,?,?,?,?,?,'best',0,0,'instagram','queued',?,?,?,2,?,?)""",(jid,url,'instagram',folder,'Instagram '+mode,subdir,now(),now(),now(),mode,owner));self.db.commit()
        return {'id':jid,'destination':'Zedair/'+(folder or ('Instagram/'+(owner or '<resolved owner>')))}

    def record_usage(self,jid,count):
        if count<=0:return
        try:domain=urllib.parse.urlsplit(self.job(jid)['url']).hostname or 'unknown'
        except APIError:return
        day=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        with self.lock:
            key=(day,domain);self.usage_pending[key]=self.usage_pending.get(key,0)+count
        self.flush_usage()

    def flush_usage(self,force=False):
        with self.lock:
            if not force and now()-self.usage_flushed<10:return
            for (day,domain),count in self.usage_pending.items():
                self.db.execute('INSERT INTO usage VALUES(?,?,?) ON CONFLICT(day,domain) DO UPDATE SET bytes=bytes+excluded.bytes',(day,domain,count))
            self.db.commit();self.usage_pending.clear();self.usage_flushed=now()

    def usage_summary(self,period):
        today=datetime.datetime.now(datetime.timezone.utc).date()
        starts={'day':today,'week':today-datetime.timedelta(days=today.weekday()),'month':today.replace(day=1),'year':today.replace(month=1,day=1)}
        if period not in starts:raise APIError('Choose day, week, month or year')
        self.flush_usage(force=True)
        return {'period':period,'timezone':'UTC','since':starts[period].isoformat(),'domains':self.rows('SELECT domain,sum(bytes) AS bytes FROM usage WHERE day>=? GROUP BY domain ORDER BY bytes DESC',(starts[period].isoformat(),)),
            'note':'Recorded website transfer payload, grouped by submitted site. Engine progress may include resumed bytes; browser traffic, protocol overhead, torrents and transfers without byte progress are excluded.'}

    def queue_notice(self,job,error):
        settings=self.settings()
        if not settings.get('telegram_errors') and not settings.get('telegram_snapshots'):return
        photo=None;path=self.root/'.incomplete'/job['id']/'.browser-error.jpg'
        if settings.get('telegram_snapshots') and path.is_file() and not path.is_symlink():
            if path.stat().st_size<=1024*1024:photo=path.read_bytes()
            path.unlink(missing_ok=True)
        if not settings.get('telegram_errors') and not photo:return
        from notifications import sanitized
        message='Download failed: '+job['name']+'\n'+sanitized(job['url'])+'\n'+sanitized(error)
        try:self.notices.put_nowait((message,photo))
        except queue.Full:pass

    def notification_loop(self):
        from notifications import send
        while not self.stop.is_set():
            try:message,photo=self.notices.get(timeout=1)
            except queue.Empty:continue
            result=send(self.settings(),message,photo)
            self.set_setting('telegram_last_result',result)

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
        elif p.path == '/api/usage':
            self.reply(d.usage_summary(query.get('period',['day'])[0]))
        elif p.path == '/api/engines':
            from maintenance import inventory
            self.reply(inventory(d))
        elif p.path == '/api/favicon':
            from maintenance import favicon
            raw,mime=favicon(d,query.get('id',[''])[0])
            self.send_response(200);self.headers_common();self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)));self.end_headers()
            if self.command!='HEAD':self.wfile.write(raw)
        elif p.path == '/api/system':
            storage=d.storage(reserve=False)
            self.reply(d.systemstats.snapshot(d.root if storage['ready'] else None))
        elif p.path == '/api/progress':
            jobs=d.rows("SELECT id,status,name,progress,items,engine FROM jobs WHERE status='running' AND remove_requested=0")
            for job in jobs:
                job.update({k:v for k,v in d.live.get(job['id'],{}).items() if k in ('progress','name','speed','eta','detail','engine')})
            self.reply({'jobs':jobs})
        elif p.path == '/api/folders':
            relative = query.get('path', [''])[0]
            path = d.destination(relative, reserve=False)
            if not path.is_dir():
                raise APIError('Folder not found', 404)
            try:
                offset=int(query.get('offset',['0'])[0]);limit=int(query.get('limit',['1000'])[0])
                if offset<0 or not 1<=limit<=1000:raise ValueError()
            except ValueError:raise APIError('Invalid file page')
            sort=query.get('sort',['date'])[0];order=query.get('order',['desc'])[0]
            if sort not in ('name','size','date') or order not in ('asc','desc'):
                raise APIError('Invalid file sort')
            entries=[]
            dates={r['name']:r for r in d.rows('SELECT name,size,mtime_ns,downloaded FROM file_dates WHERE folder=?',(relative,))}
            reserved = {d.root/r['folder']/r['subdir']/r['target'] for r in d.rows('SELECT p.target,j.folder,j.subdir FROM published p JOIN jobs j ON p.job=j.id WHERE p.complete=0')}
            # Sort the complete directory before slicing a page; never sort just 24 items.
            for item in path.iterdir():
                if item.is_symlink() or item.name.startswith('.') or item in reserved:continue
                try:
                    stat=item.stat();directory=item.is_dir()
                    if not directory and not item.is_file():continue
                    saved=dates.get(item.name)
                    recorded=not directory and saved and saved['size']==stat.st_size and saved['mtime_ns']==stat.st_mtime_ns
                    date=saved['downloaded'] if recorded else stat.st_mtime
                    entries.append({'name':item.name,'directory':directory,'size':stat.st_size if not directory else 0,
                        'date':date,'date_source':'downloaded' if recorded else 'modified',
                        'preview':'' if directory else preview_type(item.suffix),'stamp':str(stat.st_size)+':'+str(stat.st_mtime_ns)})
                except FileNotFoundError:continue
            # Deterministic tie ordering prevents repeated/missing files on adjacent pages.
            entries.sort(key=lambda e:(e['name'].casefold(),e['name']))
            entries.sort(key=lambda e:e['name'].casefold() if sort=='name' else e[sort],reverse=order=='desc')
            entries.sort(key=lambda e:not e['directory'])
            if 'adjacent' in query:
                media=[e for e in entries if e['preview']]
                selected=query['adjacent'][0]
                direction=query.get('direction',['0'])[0]
                if direction not in ('-1','0','1'):raise APIError('Invalid preview direction')
                indices=[i for i,e in enumerate(media) if e['name']==selected]
                if not indices:raise APIError('Preview file no longer exists',404)
                i=indices[0];n=i+int(direction)
                self.reply({'entry':media[n] if 0<=n<len(media) else None,'previous':i>0,'next':i+1<len(media)});return
            total=len(entries);entries=entries[offset:offset+limit]
            self.reply({'path':relative,'entries':entries,'limit':limit,'offset':offset,'total':total,'more':offset+len(entries)<total,'sort':sort,'order':order})
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
            with d.lock:
                for row in d.rows("SELECT id FROM jobs WHERE status IN ('done','cancelled')"):
                    if row['id'] in d.workers or row['id'] in d.processes:continue
                    d.db.execute('DELETE FROM published WHERE job=?',(row['id'],))
                    d.db.execute('DELETE FROM jobs WHERE id=?',(row['id'],))
                d.db.commit()
        elif path == '/api/files/manage':
            from fileops import manage_batch
            self.reply(manage_batch(d,data));return
        elif path == '/api/folders/inspect':
            from fileops import inspect_folder
            self.reply(inspect_folder(d,data));return
        elif path == '/api/file/manage':
            from fileops import manage
            self.reply(manage(d,data));return
        elif path == '/api/folders/delete':
            from fileops import delete_folder
            delete_folder(d,data)
        elif path == '/api/folders':
            d.destination(data.get('path', ''), create=True)
        elif path == '/api/hold':
            d.set_setting('hold', bool(data.get('hold')))
        elif path == '/api/settings':
            values=engine_settings.checked(data)
            for key,lo,hi in [('media_kib',0,100000),('torrent_down_kib',0,100000),('torrent_up_kib',1,100000),('reserve_gib',1,100),('playlist_limit',1,500),('retry_count',0,8),('seed_ratio',0,10)]:
                if key not in data:continue
                value=float(data[key]) if key=='seed_ratio' else int(data[key])
                if isinstance(data[key],bool) or not lo<=value<=hi:raise APIError(f'{key} must be between {lo} and {hi}')
                values[key]=value
            # Blank masked token means preserve; explicit clear is a separate flag.
            if values.get('telegram_bot_token')=='':values.pop('telegram_bot_token')
            if data.get('clear_telegram_token') is True:values['telegram_bot_token']=''
            for key,value in values.items():d.set_setting(key,value)
            try:d.apply_torrent_settings()
            except APIError:pass
        elif path == '/api/jobs/retry-all':
            with d.lock:
                for row in d.rows("SELECT id FROM jobs WHERE status='error'"):
                    if row['id'] not in d.workers:d.job_action(row['id'],'retry')
        elif path == '/api/instagram/stories':
            self.reply(d.add_stories(data));return
        elif path == '/api/telegram/test':
            if not d.get_setting('telegram_errors',False) and not d.get_setting('telegram_snapshots',False):raise APIError('Enable Telegram notifications first')
            try:d.notices.put_nowait(('Zedair test notification',None))
            except queue.Full:raise APIError('Notification queue is full; try later')
        elif path == '/api/cache/clear':
            from maintenance import clear_cache
            self.reply(clear_cache(d,data));return
        elif path == '/api/engines/update':
            from maintenance import request_update
            self.reply(request_update(d,data.get('package')));return
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
                if data.get('delete') is True:
                    from web_session import forget
                    forget(d.state,data.get('slot'))
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
