"""Direct Reddit post resolver and downloader.

Reads a post's own JSON (using the saved Reddit session so NSFW is not hidden),
finds the highest-quality v.redd.it DASH video + audio, and muxes them with FFmpeg.
Also handles galleries, direct images/GIFs and crossposts. External hosts
(RedGifs, Imgur, Streamable...) are returned as 'external' for yt-dlp.

Never executes page JavaScript. Every request is validated against SSRF rules.
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import media
import platforms

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/131.0.0.0 Safari/537.36')
REDDIT_HOSTS = ('reddit.com', 'redd.it')
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif')
EXTERNAL = ('redgifs.com', 'imgur.com', 'streamable.com', 'gfycat.com', 'giphy.com')
MAX_JSON = 8 * 1024 * 1024


def say(message):
    print('ZEDPROFILE ' + message, flush=True)


def is_reddit_host(host):
    host = (host or '').lower()
    return any(host == d or host.endswith('.' + d) for d in REDDIT_HOSTS)


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        media.validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def make_opener(jar):
    return urllib.request.build_opener(Redirects(), urllib.request.HTTPCookieProcessor(jar))


def fetch(opener, url, headers=None, limit=MAX_JSON, timeout=30):
    media.validate_url(url)
    h = {'User-Agent': USER_AGENT, 'Accept': 'application/json,text/html;q=0.8,*/*;q=0.5',
         'Accept-Language': 'en-US,en;q=0.8'}
    h.update(headers or {})
    with opener.open(urllib.request.Request(url, headers=h), timeout=timeout) as response:
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Reddit response exceeds the safe size limit')
        return raw, response.geturl()


# --------------------------------------------------------------------------- post lookup

def post_id_and_path(url):
    """Return (post_id or None, path) after following share links."""
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or '').lower()
    if host == 'redd.it' or host == 'www.redd.it':
        ident = p.path.strip('/').split('/')[0]
        return (ident, '/comments/' + ident) if re.fullmatch(r'[a-z0-9]+', ident) else (None, p.path)
    match = re.search(r'/comments/([a-z0-9]+)', p.path)
    if match:
        return match[1], p.path
    return None, p.path


def resolve_share_link(opener, url):
    """reddit.com/r/x/s/abc and similar redirect to the real permalink."""
    p = urllib.parse.urlsplit(url)
    if re.search(r'/s/[A-Za-z0-9]+/?$', p.path) or p.path.startswith('/video/'):
        _, final = fetch(opener, url, headers={'Accept': 'text/html'}, limit=64 * 1024, timeout=20)
        return final
    return url


def bearer_token(jar):
    for cookie in jar:
        if cookie.name == 'token_v2' and cookie.value and 'reddit.com' in cookie.domain \
                and (cookie.expires is None or cookie.expires > time.time()):
            return cookie.value
    return ''


def find_post(payload):
    """Extract the post dict from Reddit listing JSON."""
    if isinstance(payload, list) and payload:
        payload = payload[0]
    try:
        children = payload['data']['children']
        for child in children:
            if child.get('kind') == 't3' and isinstance(child.get('data'), dict):
                return child['data']
    except (KeyError, TypeError, AttributeError):
        pass
    return None


def load_post(opener, jar, url):
    url = resolve_share_link(opener, url)
    ident, path = post_id_and_path(url)
    if not ident:
        # v.redd.it/<id> and i.redd.it direct links carry no post ID
        return None, url
    attempts = [
        ('www JSON', 'https://www.reddit.com/comments/' + ident + '.json?raw_json=1&limit=1', {}),
        ('old JSON', 'https://old.reddit.com/comments/' + ident + '.json?raw_json=1&limit=1', {}),
    ]
    token = bearer_token(jar)
    if token:
        attempts.insert(1, ('API JSON', 'https://oauth.reddit.com/comments/' + ident + '.json?raw_json=1&limit=1',
                            {'Authorization': 'Bearer ' + token}))
    problems = []
    for label, endpoint, headers in attempts:
        try:
            raw, _ = fetch(opener, endpoint, headers=headers)
            post = find_post(json.loads(raw.decode('utf-8', 'replace')))
            if post:
                say('Reddit post read via ' + label)
                return post, url
            problems.append(label + ': no post data')
        except urllib.error.HTTPError as exc:
            problems.append(f'{label}: HTTP {exc.code}')
            if exc.code == 429:
                time.sleep(3)
        except Exception as exc:
            problems.append(f'{label}: {type(exc).__name__}: {str(exc)[:100]}')
    raise ValueError('Reddit post data unavailable (' + '; '.join(problems) +
                     '). Cookies may be stale, or Reddit is blocking this connection.')


# --------------------------------------------------------------------------- classification

def clean(url):
    return re.sub(r'&amp;', '&', url or '')


def classify(post):
    """Return a dict: kind video|gallery|image|external, plus data. Raises ValueError if none."""
    if post.get('crosspost_parent_list'):
        parent = post['crosspost_parent_list'][0]
        if isinstance(parent, dict):
            inner = classify(parent)
            inner['title'] = post.get('title') or inner.get('title')
            return inner
    title = (post.get('title') or '').strip()
    base = {'title': title, 'id': post.get('id', ''), 'over_18': bool(post.get('over_18'))}
    for holder in (post.get('secure_media'), post.get('media')):
        rv = holder.get('reddit_video') if isinstance(holder, dict) else None
        if isinstance(rv, dict) and (rv.get('dash_url') or rv.get('fallback_url') or rv.get('hls_url')):
            return {**base, 'kind': 'video', 'dash': clean(rv.get('dash_url')), 'hls': clean(rv.get('hls_url')),
                    'fallback': clean(rv.get('fallback_url')), 'height': rv.get('height') or 0}
    meta = post.get('media_metadata')
    if post.get('is_gallery') and isinstance(meta, dict):
        ordered = [i.get('media_id') for i in (post.get('gallery_data') or {}).get('items', [])] or list(meta)
        urls = []
        for mid in ordered:
            item = meta.get(mid) or {}
            s = item.get('s') or {}
            best = clean(s.get('mp4') or s.get('gif') or s.get('u'))
            if best:
                urls.append(best)
        if urls:
            return {**base, 'kind': 'gallery', 'urls': urls}
    dest = clean(post.get('url_overridden_by_dest') or post.get('url'))
    host = (urllib.parse.urlsplit(dest).hostname or '').lower()
    preview = (post.get('preview') or {}).get('reddit_video_preview')
    if isinstance(preview, dict) and preview.get('fallback_url'):
        return {**base, 'kind': 'video', 'dash': clean(preview.get('dash_url')), 'hls': clean(preview.get('hls_url')),
                'fallback': clean(preview['fallback_url']), 'height': preview.get('height') or 0}
    if any(host == d or host.endswith('.' + d) for d in EXTERNAL):
        return {**base, 'kind': 'external', 'url': dest}
    if host in ('i.redd.it', 'i.imgur.com') or urllib.parse.urlsplit(dest).path.lower().endswith(IMAGE_EXT):
        return {**base, 'kind': 'image', 'urls': [dest]}
    if host == 'v.redd.it':
        return {**base, 'kind': 'video', 'dash': dest.rstrip('/') + '/DASHPlaylist.mpd', 'hls': '', 'fallback': '', 'height': 0}
    raise ValueError('This Reddit post does not contain a video or image (text or link post).')


# --------------------------------------------------------------------------- DASH

def parse_mpd(text, mpd_url, ceiling=0):
    """Return (video_url, audio_url|None, height) with the best representation under ceiling."""
    root = ET.fromstring(text)
    tag = lambda e: e.tag.rsplit('}', 1)[-1]
    best_video = None
    best_audio = None
    for adaptation in root.iter():
        if tag(adaptation) != 'AdaptationSet':
            continue
        kind = (adaptation.get('contentType') or adaptation.get('mimeType') or '').lower()
        for rep in adaptation:
            if tag(rep) != 'Representation':
                continue
            base = next((c.text.strip() for c in rep if tag(c) == 'BaseURL' and c.text), None)
            if not base:
                base = next((c.text.strip() for c in adaptation if tag(c) == 'BaseURL' and c.text), None)
            if not base:
                continue
            rep_kind = (rep.get('mimeType') or kind).lower()
            bandwidth = int(rep.get('bandwidth') or 0)
            url = urllib.parse.urljoin(mpd_url, clean(base))
            if 'audio' in rep_kind:
                if not best_audio or bandwidth > best_audio[0]:
                    best_audio = (bandwidth, url)
            elif 'video' in rep_kind:
                height = int(rep.get('height') or 0)
                if ceiling and height and height > ceiling:
                    continue
                if not best_video or (height, bandwidth) > best_video[:2]:
                    best_video = (height, bandwidth, url)
    if not best_video:
        raise ValueError('DASH manifest listed no video track')
    return best_video[2], (best_audio[1] if best_audio else None), best_video[0]


def audio_guess(video_url, opener):
    base = video_url.rsplit('/', 1)[0] + '/'
    for name in ('DASH_AUDIO_128.mp4', 'DASH_audio.mp4', 'DASH_AUDIO_64.mp4', 'DASH_AUDIO_192.mp4'):
        try:
            req = urllib.request.Request(base + name, headers={'User-Agent': USER_AGENT, 'Range': 'bytes=0-1',
                                                               'Referer': 'https://www.reddit.com/'})
            media.validate_url(base + name)
            with opener.open(req, timeout=15):
                return base + name
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------- transfer

def download_file(opener, url, target, rate_kib=0, label='media'):
    media.validate_url(url)
    target = Path(target)
    part = target.with_name(target.name + '.part')
    headers = {'User-Agent': USER_AGENT, 'Referer': 'https://www.reddit.com/', 'Origin': 'https://www.reddit.com',
               'Accept': '*/*', 'Accept-Encoding': 'identity'}
    last = None
    for attempt in range(4):
        try:
            done = 0
            started = time.monotonic()
            tick = started
            with opener.open(urllib.request.Request(url, headers=headers), timeout=40) as response, part.open('wb') as out:
                ctype = response.headers.get('Content-Type', '').lower()
                if ctype.startswith('text/') or 'json' in ctype:
                    raise ValueError('Reddit returned a web page instead of media (' + ctype + ')')
                total = int(response.headers.get('Content-Length') or 0)
                while block := response.read(256 * 1024):
                    out.write(block)
                    done += len(block)
                    if rate_kib:
                        time.sleep(max(0, done / (rate_kib * 1024) - (time.monotonic() - started)))
                    if time.monotonic() - tick >= 1:
                        elapsed = max(0.001, time.monotonic() - started)
                        print('ZEDPROG ' + json.dumps({'downloaded_bytes': done, 'total_bytes': total, 'status': 'downloading',
                              'speed': done / elapsed, 'eta': int((total - done) / (done / elapsed)) if total and done else None,
                              'filename': label}), flush=True)
                        tick = time.monotonic()
                out.flush()
                os.fsync(out.fileno())
            if not done or (total and done != total):
                raise ValueError('Incomplete download')
            os.replace(part, target)
            return done
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (401, 403, 404):
                break
            time.sleep(2 * (attempt + 1))
        except Exception as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    part.unlink(missing_ok=True)
    raise ValueError(f'Could not download {label}: {last}')


def safe_name(title, ident):
    title = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', title or 'Reddit video')
    title = re.sub(r'\s+', ' ', title).strip(' .')
    title = title.encode()[:100].decode('utf-8', 'ignore').rstrip(' .') or 'Reddit video'
    return f'{title} [{ident}]' if ident else title


def mux(video, audio, target):
    cmd = ['ffmpeg', '-y', '-nostdin', '-loglevel', 'error', '-threads', '1', '-i', str(video)]
    if audio:
        cmd += ['-i', str(audio)]
    cmd += ['-c', 'copy', '-movflags', '+faststart', str(target)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode or not Path(target).is_file() or Path(target).stat().st_size == 0:
        raise ValueError('FFmpeg could not combine video and audio: ' + (result.stderr or '')[-300:])


def download_video(opener, info, dest, ceiling, rate_kib, audio_only=False):
    dest = Path(dest)
    name = safe_name(info['title'], info['id'])
    video_url = audio_url = None
    height = 0
    problems = []
    if info.get('dash'):
        try:
            raw, final = fetch(opener, info['dash'], headers={'Referer': 'https://www.reddit.com/', 'Accept': '*/*'}, limit=2 * 1024 * 1024)
            video_url, audio_url, height = parse_mpd(raw.decode('utf-8', 'replace'), final, ceiling)
        except Exception as exc:
            problems.append('DASH: ' + str(exc)[:120])
    if not video_url and info.get('fallback'):
        video_url = info['fallback'].split('?')[0] if 'DASH_' in info['fallback'] else info['fallback']
        m = re.search(r'DASH_(\d+)', video_url)
        height = int(m[1]) if m else info.get('height', 0)
        audio_url = audio_guess(video_url, opener)
    if not video_url:
        raise ValueError('No usable Reddit video URL (' + '; '.join(problems) + ')')
    say(f'Reddit video found: {height or "?"}p' + (' with audio' if audio_url else ' (no audio track)'))
    print('ZEDTITLE ' + info['title'][:200], flush=True)
    work = dest / '.reddit-parts'
    work.mkdir(exist_ok=True)
    try:
        vfile, afile = work / 'video.mp4', work / 'audio.mp4'
        target = dest / (name + ('.m4a' if audio_only else '.mp4'))
        if audio_only:
            if not audio_url:
                raise ValueError('This Reddit video has no audio track')
            download_file(opener, audio_url, target, rate_kib, 'audio')
            return target
        download_file(opener, video_url, vfile, rate_kib, 'video')
        if audio_url:
            try:
                download_file(opener, audio_url, afile, rate_kib, 'audio')
            except ValueError as exc:
                say('Audio track unavailable, saving video only: ' + str(exc)[:120])
                audio_url = None
        if audio_url:
            say('Combining video and audio…')
            mux(vfile, afile, target)
        else:
            os.replace(vfile, target)
        return target
    finally:
        for f in work.glob('*'):
            f.unlink(missing_ok=True)
        try:
            work.rmdir()
        except OSError:
            pass


def download_images(opener, info, dest, rate_kib):
    dest = Path(dest)
    urls = info['urls']
    name = safe_name(info['title'], info['id'])
    saved = 0
    print(f'ZEDTITLE {info["title"][:200]} · {len(urls)} items', flush=True)
    for i, url in enumerate(urls, 1):
        ext = Path(urllib.parse.urlsplit(url).path).suffix.lower()
        if ext not in IMAGE_EXT and ext != '.mp4':
            ext = '.jpg'
        target = dest / (f'{name} {i:03}{ext}' if len(urls) > 1 else f'{name}{ext}')
        if target.exists():
            saved += 1
            continue
        try:
            download_file(opener, url, target, rate_kib, f'item {i}')
            saved += 1
            print(f'Completed item {i}/{len(urls)}', flush=True)
        except ValueError as exc:
            print(f'Warning: item {i} failed: {exc}', flush=True)
    if not saved:
        raise ValueError('No images could be downloaded from this Reddit post')
    if saved < len(urls):
        raise ValueError(f'Only {saved} of {len(urls)} gallery items downloaded; retry to fetch the rest')


def resolve(url, cookie=''):
    """Return (info, jar, opener). info['kind'] in video|gallery|image|external."""
    import http.cookiejar
    jar = platforms.load_jar(Path(cookie)) if cookie else http.cookiejar.MozillaCookieJar()
    opener = make_opener(jar)
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    path = urllib.parse.urlsplit(url).path
    if host == 'v.redd.it':
        ident = path.strip('/').split('/')[0]
        return {'kind': 'video', 'title': 'Reddit video', 'id': ident, 'dash': f'https://v.redd.it/{ident}/DASHPlaylist.mpd',
                'hls': '', 'fallback': '', 'height': 0}, jar, opener
    if host == 'i.redd.it':
        ident = Path(path).stem
        return {'kind': 'image', 'title': 'Reddit image', 'id': ident, 'urls': [url]}, jar, opener
    post, final = load_post(opener, jar, url)
    if not post:
        raise ValueError('Not a Reddit post link (expected /comments/... or redd.it/...)')
    return classify(post), jar, opener


def run(url, dest, cookie, quality='best', rate_kib=0, audio_only=False):
    """Download directly. Returns None when done; ('external', url, title) for yt-dlp to handle."""
    ceiling = int(quality) if str(quality).isdigit() else 0
    info, jar, opener = resolve(url, cookie)
    say(f'Reddit post type: {info["kind"]}' + (' (NSFW)' if info.get('over_18') else ''))
    if info['kind'] == 'external':
        return ('external', info['url'], info['title'])
    if info['kind'] == 'video':
        download_video(opener, info, dest, ceiling, rate_kib, audio_only)
    else:
        download_images(opener, info, dest, rate_kib)
    return None
