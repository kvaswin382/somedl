"""qBittorrent-nox client (WebUI API v2), tuned for a 2 GB headless host.

The desk's UI and safety checks consume Transmission-shaped dictionaries
(hashString, percentDone, rateDownload, status 0-6 ...). This adapter keeps that
shape so the browser code does not change, while talking only to qBittorrent.
Standard library only; one persistent keep-alive connection to 127.0.0.1.
"""
import base64
import hashlib
import http.client
import json
import re
import threading
import time
import urllib.parse
import uuid

HASH = re.compile(r'[0-9a-fA-F]{40}')


class EngineError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


# --- qBittorrent state -> the numeric status codes the UI already understands ---
# 0 paused/stopped, 1 verify queued, 2 verifying, 3 queued, 4 downloading,
# 5 seed queued, 6 seeding.
STATUS = {
    'pausedDL': 0, 'pausedUP': 0, 'stoppedDL': 0, 'stoppedUP': 0, 'error': 0,
    'missingFiles': 0, 'unknown': 0,
    'checkingResumeData': 2, 'checkingDL': 2, 'checkingUP': 2, 'allocating': 2, 'moving': 2,
    'queuedDL': 3,
    'downloading': 4, 'metaDL': 4, 'forcedMetaDL': 4, 'forcedDL': 4, 'stalledDL': 4,
    'queuedUP': 5,
    'uploading': 6, 'stalledUP': 6, 'forcedUP': 6,
}
PROBLEMS = {
    'error': 'qBittorrent reported an error for this torrent. Check the SSD and its free space, then Verify.',
    'missingFiles': 'Downloaded files are missing. Reconnect the SSD or use Verify, or remove and re-add the torrent.',
}
NO_ETA = 8640000  # qBittorrent's "infinity" (100 days)


# --- minimal bencode reader: validates uploads and finds the info-hash ---------
def _parse(data, i, depth, spans):
    if depth > 32:
        raise ValueError('nesting too deep')
    c = data[i:i+1]
    if c == b'i':
        j = data.index(b'e', i)
        return int(data[i+1:j]), j+1
    if c == b'l':
        i += 1
        out = []
        while data[i:i+1] != b'e':
            value, i = _parse(data, i, depth+1, spans)
            out.append(value)
        return out, i+1
    if c == b'd':
        i += 1
        out = {}
        while data[i:i+1] != b'e':
            key, i = _parse(data, i, depth+1, spans)
            if not isinstance(key, bytes):
                raise ValueError('dictionary key is not a string')
            start = i
            value, i = _parse(data, i, depth+1, spans)
            if depth == 0 and key == b'info':
                spans.append((start, i))
            out[key] = value
        return out, i+1
    if c.isdigit():
        j = data.index(b':', i)
        start = j+1
        end = start+int(data[i:j])
        if end > len(data):
            raise ValueError('truncated string')
        return data[start:end], end
    raise ValueError('not bencoded data')


def bdecode(data):
    """Return (value, [byte spans of the top-level 'info' value])."""
    spans = []
    try:
        value, end = _parse(data, 0, 0, spans)
    except (ValueError, IndexError, RecursionError) as exc:
        raise ValueError('Invalid .torrent file') from exc
    if end != len(data):
        raise ValueError('Invalid .torrent file')
    return value, spans


def torrent_hash(data):
    """Info-hash (40 hex chars, as qBittorrent reports it) of a .torrent file."""
    meta, spans = bdecode(data)
    if not isinstance(meta, dict) or not spans or not isinstance(meta.get(b'info'), dict):
        raise ValueError('Invalid .torrent file')
    raw = data[spans[0][0]:spans[0][1]]
    if b'pieces' not in meta[b'info'] and b'file tree' in meta[b'info']:  # BitTorrent v2 only
        return hashlib.sha256(raw).hexdigest()[:40]
    return hashlib.sha1(raw).hexdigest()


def magnet_hash(magnet):
    """Return the btih info-hash of a magnet as 40 lowercase hex chars, or ''."""
    for value in urllib.parse.parse_qs(urllib.parse.urlsplit(magnet).query).get('xt', []):
        if value.lower().startswith('urn:btih:'):
            ident = value[9:]
            if HASH.fullmatch(ident):
                return ident.lower()
            if len(ident) == 32:
                try:
                    return base64.b32decode(ident.upper()).hex()
                except ValueError:
                    return ''
    return ''


def multipart(fields, files=()):
    boundary = '----zedair' + uuid.uuid4().hex
    out = []
    for name, value in fields.items():
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + str(value).encode() + b'\r\n')
    for name, filename, content in files:
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                   'Content-Type: application/x-bittorrent\r\n\r\n'.encode() + content + b'\r\n')
    out.append(f'--{boundary}--\r\n'.encode())
    return b''.join(out), 'multipart/form-data; boundary=' + boundary


def preferences(settings, save_path):
    """Everything the desk enforces on qBittorrent. Pure, so it can be tested.

    Memory profile for a 2 GB machine that also runs FFmpeg/yt-dlp/Chromium:
    POSIX disk I/O instead of memory-mapped files (mmap inflates RSS and the
    cgroup charge with file pages), two I/O threads, a small file pool, small
    hash-check and socket buffers, and bounded peer counts.
    """
    simultaneous = max(1, min(4, int(settings['simultaneous_downloads'])))
    return {
        # queueing: N active downloads, one seeding slot
        'queueing_enabled': True, 'max_active_downloads': simultaneous,
        'max_active_uploads': 1, 'max_active_torrents': simultaneous + 1,
        'dont_count_slow_torrents': False,
        # speed limits, bytes/s (0 = unlimited)
        'dl_limit': int(settings['torrent_down_kib']) * 1024,
        'up_limit': max(1, int(settings['torrent_up_kib'])) * 1024,
        # stop seeding at the configured ratio (0 = stop when complete)
        'max_ratio_enabled': True, 'max_ratio': float(settings['seed_ratio']), 'max_ratio_act': 0,
        'max_seeding_time_enabled': False, 'max_inactive_seeding_time_enabled': False,
        # peers
        'max_connec': 80, 'max_connec_per_torrent': 50,
        'max_uploads': 4, 'max_uploads_per_torrent': 2,
        # fixed port, no router tinkering, no LAN broadcast; DHT/PEX stay on for magnets
        'listen_port': 51413, 'random_port': False, 'upnp': False, 'lsd': False,
        'dht': True, 'pex': True, 'anonymous_mode': False,
        # files: download straight into the chosen SSD folder
        'save_path': str(save_path), 'temp_path_enabled': False, 'auto_tmm_enabled': False,
        'incomplete_files_ext': False, 'preallocate_all': False,
        'torrent_content_layout': 'Original', 'recheck_completed_torrents': False,
        # memory and I/O
        'disk_io_type': 2, 'async_io_threads': 2, 'file_pool_size': 40,
        'checking_memory_use': 16, 'send_buffer_watermark': 256,
        'send_buffer_low_watermark': 10, 'send_buffer_watermark_factor': 50,
        'socket_backlog_size': 30, 'max_concurrent_http_announces': 10,
        # fewer writes to the internal eMMC
        'save_resume_data_interval': 60,
    }


class QBittorrent:
    TIMEOUT = 8

    def __init__(self, config, error=EngineError):
        url = urllib.parse.urlsplit(config.get('rpc_url', 'http://127.0.0.1:9092'))
        self.host = url.hostname or '127.0.0.1'
        self.port = url.port or 9092
        self.user = config.get('rpc_user', 'zedair')
        self.password = config.get('rpc_password', '')
        self.error = error
        self.lock = threading.RLock()
        self.conn = None
        self.cookie = ''
        self.api = None

    # ---- transport -------------------------------------------------------------
    def _close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass
        self.conn = None

    def _send(self, method, path, body=None, content_type=None):
        if self.conn is None:
            self.conn = http.client.HTTPConnection(self.host, self.port, timeout=self.TIMEOUT)
        origin = f'http://{self.host}:{self.port}'
        headers = {'Referer': origin + '/', 'Origin': origin}
        if self.cookie:
            headers['Cookie'] = self.cookie
        if content_type:
            headers['Content-Type'] = content_type
        self.conn.request(method, '/api/v2/' + path, body, headers)
        response = self.conn.getresponse()
        return response.status, response.read(), response.getheader('Set-Cookie') or ''

    def _login(self):
        body = urllib.parse.urlencode({'username': self.user, 'password': self.password}).encode()
        status, data, cookie = self._send('POST', 'auth/login', body, 'application/x-www-form-urlencoded')
        if status == 403:
            self.cookie = ''
            raise self.error('Torrent engine temporarily blocked logins. Wait a few minutes or restart zedair-qbittorrent.', 503)
        # qBittorrent answers a wrong password with HTTP 200 and the text "Fails."
        if status != 200 or data.strip() != b'Ok.' or '=' not in cookie:
            self.cookie = ''
            raise self.error('Torrent engine rejected the saved credentials. Check zedair-qbittorrent and its configuration.', 503)
        self.cookie = cookie.split(';', 1)[0]

    def request(self, path, fields=None, files=None, post=False, form=False):
        """Return (status, body). Logs in on demand and retries stale connections once."""
        post = bool(files) or bool(post) or form
        with self.lock:
            for attempt in range(3):
                try:
                    if not self.cookie:
                        self._login()
                    if files or form:
                        body, ctype = multipart(fields or {}, files or ())
                    elif post:
                        body, ctype = urllib.parse.urlencode(fields or {}).encode(), 'application/x-www-form-urlencoded'
                    else:
                        body, ctype = None, None
                    target = path
                    if not post and fields:
                        target += '?' + urllib.parse.urlencode(fields)
                    status, data, _ = self._send('POST' if post else 'GET', target, body, ctype)
                except (OSError, http.client.HTTPException):
                    self._close()
                    self.cookie = ''
                    if attempt == 2:
                        raise self.error('Torrent engine is offline. Check zedair-qbittorrent on the laptop.', 503)
                    continue
                if status == 403 and attempt < 2:  # session expired or engine restarted
                    self.cookie = ''
                    continue
                if status == 403:
                    raise self.error('Torrent engine rejected the request. Check its service and credentials.', 503)
                return status, data
        raise self.error('Torrent engine session handshake failed', 503)

    def _json(self, path, fields=None):
        status, data = self.request(path, fields)
        if status == 404:
            raise self.error('Torrent not found', 404)
        if status != 200:
            raise self.error(f'Torrent engine answered HTTP {status}', 502)
        try:
            return json.loads(data)
        except ValueError:
            raise self.error('Torrent engine sent an unreadable response', 502)

    def _act(self, path, fields):
        status, data = self.request(path, fields, post=True)
        if status == 404:
            raise self.error('Torrent not found', 404)
        if status != 200:
            raise self.error(f'Torrent engine refused the request (HTTP {status})', 502)
        return data.decode('utf-8', 'replace').strip()

    # ---- helpers ---------------------------------------------------------------
    def valid(self, ident):
        """Only exact 40-hex hashes. qBittorrent treats 'all' and '|' lists specially."""
        if not isinstance(ident, str) or not HASH.fullmatch(ident):
            raise self.error('Torrent not found', 404)
        return ident.lower()

    def _verbs(self):
        if self.api is None:
            status, data = self.request('app/webapiVersion')
            try:
                self.api = tuple(int(x) for x in data.decode().strip().split('.')[:2]) if status == 200 else (2, 9)
            except ValueError:
                self.api = (2, 9)
        # WebUI API 2.11 (qBittorrent 5.x) renamed pause/resume to stop/start.
        return ('stop', 'start') if self.api >= (2, 11) else ('pause', 'resume')

    @staticmethod
    def normal(t):
        state = t.get('state', 'unknown')
        eta = t.get('eta', -1)
        return {
            'hashString': t['hash'], 'name': t.get('name', ''), 'status': STATUS.get(state, 0),
            'percentDone': max(0.0, min(1.0, float(t.get('progress', 0) or 0))),
            'rateDownload': t.get('dlspeed', 0), 'rateUpload': t.get('upspeed', 0),
            'eta': -1 if eta is None or eta < 0 or eta >= NO_ETA else eta,
            'totalSize': t.get('total_size') or t.get('size') or 0,
            'errorString': PROBLEMS.get(state, ''), 'uploadRatio': max(0.0, float(t.get('ratio', 0) or 0)),
            'downloadDir': t.get('save_path', ''),
            'metadataPercentComplete': 0 if state in ('metaDL', 'forcedMetaDL') else 1,
            'queuePosition': t.get('priority', 0),
        }

    # ---- reads -----------------------------------------------------------------
    def list(self):
        return [self.normal(t) for t in self._json('torrents/info')]

    def get(self, ident):
        """One torrent's summary (a single cheap call)."""
        rows = self._json('torrents/info', {'hashes': self.valid(ident)})
        if not rows:
            raise self.error('Torrent not found', 404)
        return self.normal(rows[0])

    def detail(self, ident):
        """Summary plus per-file sizes, progress and whether each file is wanted."""
        result = self.get(ident)
        files = self._json('torrents/files', {'hash': result['hashString']})
        result['files'] = [{'name': f['name'], 'length': f['size'], 'bytesCompleted': int(f['size']*f.get('progress', 0))} for f in files]
        result['fileStats'] = [{'wanted': f.get('priority', 1) != 0} for f in files]
        return result

    def managed(self):
        """Every torrent with its file names; used to protect files from deletion."""
        result = []
        for t in self._json('torrents/info'):
            try:
                files = self._json('torrents/files', {'hash': t['hash']})
            except Exception as exc:
                if getattr(exc, 'status', None) == 404:  # removed while we were looking
                    continue
                raise
            result.append({'downloadDir': t.get('save_path', ''), 'files': [{'name': f['name']} for f in files]})
        return result

    # ---- changes ---------------------------------------------------------------
    def add(self, folder, magnet=None, metainfo=None, paused=False):
        fields = {'savepath': str(folder), 'autoTMM': 'false', 'skip_checking': 'false',
                  'sequentialDownload': 'false', 'firstLastPiecePrio': 'false', 'contentLayout': 'Original',
                  # 4.x calls it "paused", 5.x "stopped"; the unused name is ignored
                  'paused': 'true' if paused else 'false', 'stopped': 'true' if paused else 'false'}
        files = ()
        if magnet:
            ident = magnet_hash(magnet)
            fields['urls'] = magnet
        else:
            ident = torrent_hash(metainfo)
            files = (('torrents', 'upload.torrent', metainfo),)
        if ident and any(t['hash'].lower() == ident for t in self._json('torrents/info', {'hashes': ident})):
            raise self.error('This torrent is already in the list.', 409)
        status, data = self.request('torrents/add', fields, files or None, post=True, form=True)
        text = data.decode('utf-8', 'replace').strip()
        if status == 415:
            raise self.error('qBittorrent rejected this .torrent file.', 400)
        if status != 200 or text != 'Ok.':
            raise self.error('qBittorrent did not accept this torrent. It may already exist or be invalid.', 409)
        # qBittorrent answers "Ok." before the torrent is registered; follow-up calls would 404.
        for _ in range(50):
            if not ident or self._json('torrents/info', {'hashes': ident}):
                break
            time.sleep(0.1)
        return ident

    def stop(self, hashes):
        self._toggle(0, hashes)

    def start(self, hashes):
        self._toggle(1, hashes)

    def _toggle(self, index, hashes):
        ids = '|'.join(self.valid(h) for h in hashes)
        if not ids:
            return
        verbs = self._verbs()
        status, _ = self.request('torrents/' + verbs[index], {'hashes': ids}, post=True)
        if status == 404:  # version detection was wrong; try the other spelling
            other = ('pause', 'resume') if verbs[0] == 'stop' else ('stop', 'start')
            status, _ = self.request('torrents/' + other[index], {'hashes': ids}, post=True)
            if status == 200:
                self.api = (2, 9) if other[0] == 'pause' else (2, 11)
        if status != 200:
            raise self.error(f'Torrent engine refused the request (HTTP {status})', 502)

    def remove(self, ident):
        """Remove from the list; downloaded files always stay on the SSD."""
        self._act('torrents/delete', {'hashes': self.valid(ident), 'deleteFiles': 'false'})

    def recheck(self, ident):
        self._act('torrents/recheck', {'hashes': self.valid(ident)})

    def set_files(self, ident, wanted, count):
        """Download only the listed file indexes; an empty list deselects everything."""
        ident = self.valid(ident)
        chosen = set(wanted)
        unwanted = [i for i in range(count) if i not in chosen]
        if wanted:  # raise first, so the torrent never has every file skipped midway
            self._act('torrents/filePrio', {'hash': ident, 'id': '|'.join(map(str, sorted(chosen))), 'priority': 1})
        if unwanted:
            self._act('torrents/filePrio', {'hash': ident, 'id': '|'.join(map(str, unwanted)), 'priority': 0})

    def apply_settings(self, settings, save_path):
        self._act('app/setPreferences', {'json': json.dumps(preferences(settings, save_path))})
