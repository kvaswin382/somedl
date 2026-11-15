"""Helpers shared by torrent tests: a tiny bencoder and a .torrent builder."""
import hashlib
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def benc(x):
    if isinstance(x, bool):
        raise TypeError('bool')
    if isinstance(x, int):
        return b'i%de' % x
    if isinstance(x, str):
        x = x.encode()
    if isinstance(x, bytes):
        return b'%d:%s' % (len(x), x)
    if isinstance(x, list):
        return b'l' + b''.join(benc(i) for i in x) + b'e'
    if isinstance(x, dict):
        items = sorted((k.encode() if isinstance(k, str) else k, v) for k, v in x.items())
        return b'd' + b''.join(benc(k) + benc(v) for k, v in items) + b'e'
    raise TypeError(type(x))


def make_torrent(root, name, files, piece=16384):
    """Write `files` [(relative path, bytes)] under root/name and return (torrent bytes, info-hash)."""
    base = root / name
    blob = b''
    for rel, data in files:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        blob += data
    pieces = b''.join(hashlib.sha1(blob[i:i+piece]).digest() for i in range(0, len(blob), piece))
    info = {'name': name, 'piece length': piece, 'pieces': pieces,
            'files': [{'length': len(d), 'path': r.split('/')} for r, d in files]}
    return benc({'announce': 'http://127.0.0.1:9/announce', 'info': info}), hashlib.sha1(benc(info)).hexdigest()


class FakeQBittorrent:
    """Just enough of qBittorrent's WebUI API v2 to test the client over real HTTP."""

    def __init__(self, user='zedair', password='secret', api='2.9.3'):
        self.user, self.password, self.api = user, password, api
        self.torrents = {}   # hash -> info dict (as torrents/info returns it)
        self.files = {}      # hash -> list of file dicts
        self.calls = []      # (path, fields) for every non-login request
        self.logins = 0
        self.session = None
        self.add_result = 'Ok.'
        self.stale_connections = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def reply(self, status=200, body=b'', headers=()):
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                for key, value in headers:
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def handle_any(self, method):
                parts = urllib.parse.urlsplit(self.path)
                path = parts.path.removeprefix('/api/v2/')
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length) if length else b''
                fields = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
                ctype = self.headers.get('Content-Type', '')
                if method == 'POST' and ctype.startswith('application/x-www-form-urlencoded'):
                    fields.update({k: v[0] for k, v in urllib.parse.parse_qs(raw.decode()).items()})
                if method == 'POST' and ctype.startswith('multipart/form-data'):
                    fields['__multipart__'] = raw
                if path == 'auth/login':
                    outer.logins += 1
                    if fields.get('username') == outer.user and fields.get('password') == outer.password:
                        outer.session = 'token%d' % outer.logins
                        return self.reply(200, b'Ok.', [('Set-Cookie', f'SID={outer.session}; HttpOnly; path=/')])
                    return self.reply(200, b'Fails.')
                if not outer.session or self.headers.get('Cookie') != f'SID={outer.session}':
                    return self.reply(403, b'Forbidden')
                outer.calls.append((path, fields, self.headers.get('Referer'), self.headers.get('Origin'), method))
                if path == 'app/webapiVersion':
                    return self.reply(200, outer.api.encode())
                if path == 'torrents/info':
                    wanted = fields.get('hashes')
                    rows = [t for h, t in outer.torrents.items() if not wanted or h == wanted]
                    return self.reply(200, json.dumps(rows).encode())
                if path == 'torrents/files':
                    if fields.get('hash') not in outer.files:
                        return self.reply(404, b'Not Found')
                    return self.reply(200, json.dumps(outer.files[fields['hash']]).encode())
                if path == 'torrents/add':
                    return self.reply(200, outer.add_result.encode())
                new_api = tuple(int(x) for x in outer.api.split('.')[:2]) >= (2, 11)
                if path in ('torrents/pause', 'torrents/resume') and new_api:
                    return self.reply(404, b'Not Found')
                if path in ('torrents/stop', 'torrents/start') and not new_api:
                    return self.reply(404, b'Not Found')
                return self.reply(200, b'')

            def do_GET(self):
                self.handle_any('GET')

            def do_POST(self):
                self.handle_any('POST')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def config(self, **extra):
        return {'rpc_url': self.url, 'rpc_user': self.user, 'rpc_password': self.password, **extra}

    def add_torrent(self, ident, name='Sample', save_path='/x', state='pausedUP', progress=1.0, files=2, wanted=None, **extra):
        self.torrents[ident] = {'hash': ident, 'name': name, 'state': state, 'progress': progress,
                                'dlspeed': 0, 'upspeed': 0, 'eta': 8640000, 'size': 100, 'total_size': 100,
                                'ratio': 0.5, 'save_path': save_path, 'priority': 0, **extra}
        self.files[ident] = [{'index': i, 'name': f'{name}/f{i}.bin', 'size': 50, 'progress': progress,
                              'priority': 0 if wanted is not None and i not in wanted else 1} for i in range(files)]

    def paths(self):
        return [c[0] for c in self.calls]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
