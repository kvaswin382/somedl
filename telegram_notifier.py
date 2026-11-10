"""Telegram notifications and browser snapshots.

Works on networks where IPv6 is advertised but broken (retries over IPv4), reports a
clear reason per chat, and never raises into the caller.
"""
import http.client
import json
import socket
import ssl
import struct
import urllib.error
import urllib.request
import uuid
import zlib

API = 'https://api.telegram.org'
USER_AGENT = 'Zedair/1.1'


class _IPv4Connection(http.client.HTTPSConnection):
    def connect(self):
        last = None
        for family, kind, proto, _, address in socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM):
            try:
                sock = socket.socket(family, kind, proto)
                sock.settimeout(self.timeout)
                sock.connect(address)
                self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as exc:
                last = exc
        raise last or OSError('No IPv4 address for ' + str(self.host))


class _IPv4Handler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_IPv4Connection, req)


def _open(req, timeout):
    """Normal request first; on a connection-level failure retry once over IPv4 only.

    The first attempt uses a short timeout: with broken IPv6 the default route can hang for
    the whole timeout before the IPv4 retry even starts.
    """
    try:
        return urllib.request.urlopen(req, timeout=min(timeout, 8))
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError):
        return urllib.request.build_opener(_IPv4Handler()).open(req, timeout=timeout)


def _hint(description):
    low = str(description).lower()
    if 'chat not found' in low:
        return (' - the bot cannot see this chat. For a group: add the bot to the group and use the ID that starts with -100. '
                'For a user: open the bot in Telegram and press Start first.')
    if 'unauthorized' in low:
        return ' - the bot token is wrong or was revoked (check @BotFather).'
    if 'forbidden' in low or 'blocked' in low or 'kicked' in low:
        return ' - the bot was blocked or removed from this chat.'
    if 'not enough rights' in low or 'have no rights' in low:
        return ' - give the bot permission to post media in the group.'
    return ''


def _recipients(chat_ids):
    seen = []
    for c in chat_ids or []:
        c = str(c or '').strip()
        if c and c not in seen:
            seen.append(c)
    return seen


def _error_text(exc):
    if isinstance(exc, urllib.error.HTTPError):
        try:
            description = json.loads(exc.read().decode('utf-8', 'replace')).get('description') or exc.reason
        except Exception:
            description = f'HTTP {exc.code} {exc.reason}'
        return str(description) + _hint(description)
    return f'{type(exc).__name__}: {exc}'


def _call(token, method, body, content_type, timeout):
    req = urllib.request.Request(f'{API}/bot{token}/{method}', data=body,
                                 headers={'Content-Type': content_type, 'User-Agent': USER_AGENT})
    with _open(req, timeout) as resp:
        data = json.loads(resp.read().decode('utf-8', 'replace'))
    if not data.get('ok'):
        description = data.get('description', 'Telegram rejected the request')
        raise RuntimeError(str(description) + _hint(description))
    return data


def send_message(bot_token, chat_ids, text):
    bot_token = str(bot_token or '').strip()
    if not bot_token:
        return {'ok': False, 'error': 'Bot token is required'}
    recipients = _recipients(chat_ids)
    if not recipients:
        return {'ok': False, 'error': 'Enter a private log group ID or admin user ID'}
    errors, successes = [], []
    for cid in recipients:
        try:
            payload = json.dumps({'chat_id': cid, 'text': str(text)[:4000]}).encode('utf-8')
            _call(bot_token, 'sendMessage', payload, 'application/json', 15)
            successes.append(cid)
        except Exception as exc:
            errors.append(f'{cid}: {_error_text(exc)}')
    if not successes and errors:
        return {'ok': False, 'error': '; '.join(errors)}
    return {'ok': True, 'successes': successes, 'errors': errors}


def _multipart(fields, filename, content, mime):
    boundary = '----ZedairTelegram' + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8'))
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
                  f'Content-Type: {mime}\r\n\r\n').encode('utf-8'))
    parts.append(content)
    parts.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def send_snapshot(bot_token, chat_ids, photo_bytes, caption='', mime='image/jpeg', filename='snapshot.jpg'):
    """Send a photo to every recipient. Returns {'ok','successes','errors'} and logs each outcome."""
    bot_token = str(bot_token or '').strip()
    recipients = _recipients(chat_ids)
    if not bot_token or not photo_bytes or not recipients:
        print('ZEDPROFILE Telegram snapshot skipped: missing bot token, recipient or image', flush=True)
        return {'ok': False, 'successes': [], 'errors': ['missing bot token, recipient or image']}
    successes, errors = [], []
    for cid in recipients:
        try:
            body, ctype = _multipart({'chat_id': cid, 'caption': str(caption)[:950]}, filename, photo_bytes, mime)
            _call(bot_token, 'sendPhoto', body, ctype, 30)
            successes.append(cid)
            print(f'ZEDPROFILE Telegram snapshot sent to {cid}', flush=True)
        except Exception as exc:
            errors.append(f'{cid}: {_error_text(exc)}')
            print(f'ZEDPROFILE Telegram snapshot to {cid} failed: {_error_text(exc)}', flush=True)
    return {'ok': bool(successes), 'successes': successes, 'errors': errors}


def test_image():
    """Small generated PNG (no imaging libraries needed) to verify the photo upload path."""
    w, h = 160, 90
    rows = b''.join(b'\x00' + bytes(v for x in range(w) for v in (30 + x * 90 // w, 90, 160 - y)) for y in range(h))

    def chunk(tag, data):
        body = tag + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
