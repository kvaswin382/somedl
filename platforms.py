"""Platform preferences and private cookie storage; no values are returned to the UI."""
import copy
import http.cookiejar
import json
import os
import re
import time
import warnings
from pathlib import Path
from urllib.parse import urlsplit

QUALITY = ('best', '2160', '1080', '720', '480', 'audio')
DEFAULTS = {
    'youtube': {'enabled': True, 'folder': 'YouTube', 'quality': 'best', 'subtitles': False, 'playlist': False, 'playlist_limit': 100},
    'instagram': {'enabled': True, 'folder': 'Instagram', 'require_cookies': True, 'warn_images': True, 'videos': 'dash', 'sleep_request': 8},
    'x': {'enabled': True, 'folder': 'X', 'quality': 'best', 'api': 'graphql'},
    'threads': {'enabled': True, 'folder': 'Threads'},
    'other': {'enabled': True, 'folder': '', 'quality': 'best', 'subtitles': False},
}
DOMAINS = {'instagram': ('instagram.com',), 'youtube': ('youtube.com', 'google.com'),
           'x': ('x.com', 'twitter.com'), 'threads': ('threads.com', 'threads.net', 'instagram.com')}
RAW_FIELDS = {'instagram': ('sessionid', 'csrftoken', 'ds_user_id'), 'x': ('auth_token', 'ct0')}


def platform_for(url):
    host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
    for key, domains in [('youtube',('youtube.com','youtu.be')),('instagram',('instagram.com',)),
                         ('x',('x.com','twitter.com')),('threads',('threads.net','threads.com'))]:
        if any(host == d or host.endswith('.'+d) for d in domains):
            return key
    return 'other'


def platform_folder(url, prefs):
    key = platform_for(url)
    return prefs[key]['folder'] or (urlsplit(url).hostname or 'Other').lower().removeprefix('www.')


def preferences(saved):
    result = copy.deepcopy(DEFAULTS)
    for key, values in (saved or {}).items():
        if key in result and isinstance(values, dict):
            result[key].update({k:v for k,v in values.items() if k in result[key]})
    return result


def validate_preferences(key, values, safe_path):
    if key not in DEFAULTS or not isinstance(values, dict):
        raise ValueError('Unknown platform')
    result = {}
    for name, default in DEFAULTS[key].items():
        value = values.get(name, default)
        if isinstance(default, bool):
            if type(value) is not bool:
                raise ValueError(name+' must be on or off')
        elif name == 'folder':
            value = safe_path(value)
            if not value and key != 'other':
                raise ValueError('Choose a platform folder')
        elif name == 'quality':
            if value not in QUALITY:
                raise ValueError('Unknown quality preset')
        elif name == 'api':
            allowed = ('auto','rest','graphql') if key == 'instagram' else ('graphql','legacy','syndication')
            if value not in allowed:
                raise ValueError('Unknown extraction API')
        elif name == 'videos' and value not in ('dash','merged'):
            raise ValueError('Unknown Instagram video mode')
        elif name in ('sleep_request','playlist_limit'):
            if type(value) is not int or not (6 <= value <= 60 if name == 'sleep_request' else 1 <= value <= 500):
                raise ValueError('Platform limit is outside the supported range')
        result[name] = value
    return result


def cookie_path(state, slot):
    if slot not in DEFAULTS:
        raise ValueError('Unknown cookie profile')
    return Path(state)/('cookies-'+slot+'.txt')


def load_jar(path):
    jar = http.cookiejar.MozillaCookieJar(str(path))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def cookie_status(state, slot):
    path = cookie_path(state, slot)
    result = {'present': path.exists(), 'usable': False, 'expired': 0, 'count': 0, 'names': [], 'updated': None}
    if not path.exists():
        return result
    try:
        jar = load_jar(path)
        valid = [c for c in jar if c.expires is None or c.expires > time.time()]
        result.update(count=len(list(jar)), expired=len(list(jar))-len(valid), names=sorted({c.name for c in valid}),
                      usable=bool(valid), updated=path.stat().st_mtime)
        if slot == 'instagram':
            result['usable'] = any(c.name == 'sessionid' and c.value for c in valid)
        elif slot == 'x':
            result['usable'] = all(any(c.name == n and c.value for c in valid) for n in ('auth_token','ct0'))
    except Exception:
        result['error'] = 'Cookie file cannot be parsed. Replace it in Settings → Platforms.'
    return result


def save_cookies(state, slot, data):
    path = cookie_path(state, slot)
    temporary = path.with_suffix('.new')
    if data.get('delete'):
        path.unlink(missing_ok=True)
        return
    if data.get('fields') is not None:
        if slot not in RAW_FIELDS or not isinstance(data['fields'], dict):
            raise ValueError('Manual session fields are available for Instagram and X')
        fields = {}
        for name in RAW_FIELDS[slot]:
            value = str(data['fields'].get(name, '')).strip()
            if value.startswith(name+'='):
                value = value[len(name)+1:]
            if re.search(r'[\x00-\x20\x7f;]', value) or len(value)>8192:
                raise ValueError('Paste only the cookie value, without spaces, line breaks or semicolons')
            if value:
                fields[name] = value
        required = ('sessionid',) if slot=='instagram' else ('auth_token','ct0')
        if any(not fields.get(k) for k in required):
            raise ValueError('Required session fields: '+', '.join(required))
        # Empty expiry is the Netscape representation of a session cookie (not an expired epoch-zero cookie).
        content = '# Netscape HTTP Cookie File\n' + ''.join(
            f'.{domain}\tTRUE\t/\tTRUE\t\t{name}\t{value}\n'
            for domain in DOMAINS[slot] for name,value in fields.items())
    else:
        content = data.get('content','')
        if not isinstance(content,str) or len(content.encode())>1024*1024:
            raise ValueError('Cookie file must be smaller than 1 MiB')
        content = content.lstrip('\ufeff').replace('\r\n','\n')
        if not content.startswith(('# Netscape HTTP Cookie File','# HTTP Cookie File')):
            raise ValueError('Use a Netscape-format cookies.txt file or the manual session fields')
    try:
        fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
        with os.fdopen(fd,'w') as out:
            out.write(content)
        try:
            jar = load_jar(temporary)
        except Exception:
            raise ValueError('Invalid cookies.txt format. Export it again; no cookie values were logged.') from None
        if not list(jar):
            raise ValueError('The file contains no cookies')
        if slot in DOMAINS:
            for cookie in list(jar):
                domain = cookie.domain.lstrip('.').lower()
                if not any(domain == d or domain.endswith('.'+d) for d in DOMAINS[slot]):
                    jar.clear(cookie.domain,cookie.path,cookie.name)
        if not list(jar):
            raise ValueError('No cookies match this platform; select the correct profile')
        # Browsers commonly export session cookies with expiry 0; normalize before engine use.
        for cookie in jar:
            if cookie.expires == 0:
                cookie.expires = None
                cookie.discard = True
        jar.save(str(temporary), ignore_discard=True, ignore_expires=True)
        temporary.chmod(0o600)
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def redact(text, state):
    text = str(text)
    for path in Path(state).glob('cookies-*.txt'):
        try:
            values = sorted({c.value for c in load_jar(path) if len(c.value)>=4}, key=len, reverse=True)
            for value in values:
                text = text.replace(value, '[redacted]')
        except Exception:
            pass
    text = re.sub(r'(?i)((?:sessionid|auth_token|csrftoken|ct0|authorization)[\s\"\x27:=]+)[^\s;,\"\x27]+', r'\1[redacted]', text)
    return text


def failure_kind(message):
    low = message.lower()
    if any(s in low for s in ('429','too many requests','rate limit','please wait a few minutes')):
        return 'rate_limit', 'Website rate limit. Wait before retrying; automatic retries are paused.'
    if any(s in low for s in ('login required','loginrequired','authenticationerror','authentication required',
                              'not logged in','log in to','sign in to','sign-in','cookies are no longer valid',
                              'cookie has expired','cookies have expired','login_required','checkpoint_required',
                              'challenge_required','consent_required','invalid session','sessionid is required',
                              'http error 401','401 unauthorized')):
        return 'auth', 'Sign-in required. Open Settings → Platforms, replace the session cookies, then retry.'
    if any(s in low for s in ('unsupported url','no video could be found','no video formats found',
                              'does not contain a video','this tweet is unavailable','404 not found')):
        return 'unsupported', 'No accessible supported media. Check the link and platform availability.'
    if any(s in low for s in ('403 forbidden','http error 403','no downloadable media','threads did not expose media','reserved output file changed','published output file is missing')):
        return 'access', 'Website access or output needs attention. Check the error details before retrying.'
    return 'transient', 'Automatic retry scheduled'
