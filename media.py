"""Download adapters. No web framework; download engines run in separate processes."""
import html
import http.cookiejar
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
import platforms
import profiles


def validate_url(url, dns=True):
    if not isinstance(url, str) or len(url) > 8000:
        raise ValueError('Invalid or overly long URL')
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        raise ValueError('Use an http(s) link without embedded login credentials')
    if p.port not in (None, 80, 443):
        raise ValueError('Only standard web ports are supported')
    if p.hostname.lower() in ('localhost', 'localhost.localdomain'):
        raise ValueError('Local network download URLs are not allowed')
    try:
        addresses = [ipaddress.ip_address(p.hostname)]
    except ValueError:
        if not dns:
            return url
        try:
            addresses = [ipaddress.ip_address(i[4][0]) for i in socket.getaddrinfo(p.hostname, p.port or 443)]
        except socket.gaierror:
            raise ValueError('Cannot resolve this website; check your internet connection')
    if any(not a.is_global for a in addresses):
        raise ValueError('Local and reserved network destinations are not allowed')
    return url


def engine_for(url):
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    if host == 'instagram.com' or host.endswith('.instagram.com'):
        return 'instagram'
    if host in ('threads.net', 'threads.com') or host.endswith(('.threads.net', '.threads.com')):
        return 'threads'
    return 'video'


def video_format(quality='best',mp4=False):
    if quality=='audio':return 'ba/b'
    ceiling=f'[height<={quality}]' if quality in ('2160','1080','720','480') else ''
    if mp4:return f'bv*[ext=mp4]{ceiling}+ba[ext=m4a]/b[ext=mp4]{ceiling}'
    return f'bv*{ceiling}+ba/b{ceiling}'


def build_command(job, dest, cookie, settings, state):
    """Use argument arrays only. Every job owns one resumable destination directory."""
    url, kind = job['url'], job['kind']
    key = platforms.platform_for(url)
    prefs = platforms.preferences(settings.get('platforms'))[key]
    if job.get('profile_mode') and key=='facebook':
        return profiles.facebook_command(job,dest,cookie,settings,state)
    if kind == 'threads':
        return [sys.executable, str(Path(__file__).resolve()), '--threads', url, str(dest), str(cookie or '')]
    if kind == 'instagram':
        config = {
            'extractor': {
                'base-directory': str(dest), 'directory': [],
                'path-restrict': 'windows', 'path-replace': '_',
                'retries': 3, 'timeout': 30, 'cookies-update': True,
                'instagram': {'videos': prefs['videos'], 'previews': False, 'max-posts': 1,
                              'filename': '{post_shortcode}_{num:>02}.{extension}',
                              'warn-images': prefs['warn_images'], 'wd': False, 'sleep-request': prefs['sleep_request']},
            },
            'downloader': {'http': {'rate': str(settings['media_kib']) + 'k' if settings['media_kib'] else None},
                           'ytdl': {'options': {'format': 'bv*+ba/b', 'noplaylist': True,
                                               'concurrent_fragment_downloads': 1}}},
            'cache': {'file': str(state / 'gallery-cache.sqlite3')},
            'output': {'progress': False},
        }
        if job.get('profile_mode'):
            config['extractor']['instagram']['max-posts'] = None
            config['extractor']['instagram']['include'] = {'posts':['photos'],'reels':['reels'],'everything':['posts','reels'],'avatar':['avatar']}[job['profile_mode']]
            config['extractor']['archive'] = str(dest/'.profile-downloads-v2.sqlite3')
            config['extractor']['archive-event'] = ['file']
            config['extractor']['archive-pragma'] = ['journal_mode=WAL']
        if job.get('profile_mode'):
            ig=config['extractor']['instagram']
            minimum=max(10,prefs['sleep_request'])
            ig.update({'sleep-request':f'{minimum}-{minimum+15}','sleep-download':'15-40',
                       'sleep-retries':'60-120','sleep-429':60,'retries':1,'timeout':45,
                       'videos':False if job.get('images_only') or job['profile_mode']=='avatar' else prefs['videos']})
            config['extractor']['postprocessors']=[{'name':'metadata','mode':'json'}]
            if cookie:ig['cookies']=str(cookie)
            path=state/('profile-'+job['id']+'.json')
            path.write_text(json.dumps({'url':url,'dest':str(dest),'mode':job['profile_mode'],
                'images_only':bool(job.get('images_only')),'config':config}))
            path.chmod(0o600)
            return [sys.executable,str(Path(__file__).with_name('instagram_profile.py')),str(path)]
        # A single post/reel is intentional; it prevents an accidental whole-account crawl.
        if not job.get('profile_mode') and not re.search(r'/(p|reel|reels|tv)/[^/]+', urllib.parse.urlsplit(url).path):
            raise ValueError('For Instagram, paste a single post, carousel or reel link')
        config_path = state / ('gallery-' + job['id'] + '.json')
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        args = [sys.executable, '-m', 'gallery_dl', '--config-ignore', '--config', str(config_path),
                '--directory', str(dest), '--write-metadata']
        if job.get('profile_mode'):
            # Lowercase --print disables downloads AND can populate the archive.
            args += ['--Print','after:ZEDITEM {post_shortcode}']
        if cookie:
            args += ['--cookies', str(cookie)]
        return args + ['--', url]
    quality = job['quality']
    mp4 = job.get('container')=='mp4' and quality!='audio'
    fmt = video_format(quality,mp4)
    args = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-cache-dir',
            '--newline', '--progress', '--no-colors', '--windows-filenames', '--trim-filenames', '160',
            '--continue', '--write-info-json', '--no-overwrites', '--retries', '3', '--fragment-retries', '3',
            '--socket-timeout', '30', '--concurrent-fragments', '1',
            '--progress-delta', '1', '--progress-template',
            'download:ZEDPROG %(progress)j', '--print', 'before_dl:ZEDTITLE %(title)s',
            '--no-simulate', '-f', fmt, '-P', str(dest), '-P', f'temp:{dest}',
            '-o', '%(title).120B [%(id)s].%(ext)s',
            '--postprocessor-args', 'ffmpeg:-threads 1',
            '--yes-playlist' if job['playlist'] else '--no-playlist',
            '--playlist-end', str(prefs.get('playlist_limit',settings['playlist_limit']))]
    if mp4:
        args += ['--merge-output-format','mp4','--remux-video','mp4']
    if key == 'x':
        args += ['--extractor-args', 'twitter:api='+prefs['api']]
    if quality == 'audio':
        args += ['-x', '--audio-format', 'best']
    if job.get('subtitles'):
        args += ['--write-subs', '--write-auto-subs', '--sub-langs', 'en.*,ml.*', '--sub-format', 'srt/vtt/best']
    if settings['media_kib']:
        args += ['--limit-rate', str(settings['media_kib']) + 'K']
    if cookie:
        args += ['--cookies', str(cookie)]
    args += ['--',url]
    return args


class JSONScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.inside = False
        self.current = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('type') in ('application/json', 'application/ld+json'):
            self.inside = True
            self.current = []

    def handle_data(self, data):
        if self.inside:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.inside:
            try:
                self.documents.append(json.loads(''.join(self.current)))
            except (ValueError, RecursionError):
                pass
            self.inside = False


def threads_media(page, shortcode):
    """Only read the requested post's structured media, never recommendations or OG thumbnails."""
    parser = JSONScripts()
    parser.feed(page)
    matches = []
    stack = list(parser.documents)
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get('code') == shortcode or node.get('shortcode') == shortcode:
                matches.append(node)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    found = []
    for post in matches:
        for item in post.get('carousel_media') or [post]:
            videos = item.get('video_versions') or []
            images = (item.get('image_versions2') or {}).get('candidates') or []
            candidates = videos or images
            candidates = [c for c in candidates if isinstance(c, dict) and isinstance(c.get('url'), str)]
            if candidates:
                best = max(candidates, key=lambda c: (int(c.get('width') or 0) * int(c.get('height') or 0), int(c.get('bitrate') or 0)))
                pair = (html.unescape(best['url']), 'mp4' if videos else 'jpg')
                if pair not in found:
                    found.append(pair)
    if not found:
        raise ValueError('Threads did not expose media for this exact post. Try fresh Threads cookies; login walls and changed page formats are not supported. No thumbnails were substituted.')
    return found


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_threads(url, dest, cookies):
    match = re.search(r'/(?:post|t)/([\w-]+)', urllib.parse.urlsplit(url).path)
    if not match:
        raise ValueError('Use a direct Threads post URL, not a profile or feed')
    validate_url(url)
    jar = http.cookiejar.MozillaCookieJar()
    if cookies:
        jar.load(cookies, ignore_discard=True, ignore_expires=False)
    opener = urllib.request.build_opener(SafeRedirect(), urllib.request.HTTPCookieProcessor(jar))
    headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
               'Accept-Language': 'en-US,en;q=0.8'}
    with opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
        raw = response.read(12 * 1024 * 1024 + 1)
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError('Threads page exceeds the safe parsing limit')
    entries = threads_media(raw.decode('utf-8', 'replace'), match[1])
    print(f'ZEDTITLE Threads {match[1]} · {len(entries)} items', flush=True)
    for i, (media_url, ext) in enumerate(entries, 1):
        validate_url(media_url)
        target = Path(dest) / f'threads_{match[1]}_{i:03}.{ext}'
        if target.exists():
            continue
        temp = target.with_suffix(target.suffix + '.part')
        # CDN links can change between retries; restart this one file rather than append wrong bytes.
        start = time.monotonic()
        done = 0
        with opener.open(urllib.request.Request(media_url, headers=headers), timeout=30) as response, temp.open('wb') as out:
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith(('image/', 'video/', 'application/octet-stream')):
                raise ValueError('Threads returned a non-media response')
            total = int(response.headers.get('Content-Length', 0))
            while block := response.read(128 * 1024):
                out.write(block)
                done += len(block)
                # Every file reports bytes; the main worker still owns disk checks/cancellation.
                if time.monotonic() - start > 1:
                    print('ZEDPROG ' + json.dumps({'downloaded_bytes': done, 'total_bytes': total,
                          'filename': target.name, 'status': 'downloading'}), flush=True)
                    start = time.monotonic()
        if total and done != total:
            raise ValueError('Incomplete Threads media response; retrying is safe')
        os.replace(temp, target)
        print(f'Completed item {i}/{len(entries)}', flush=True)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 5 or sys.argv[1] != '--threads':
            raise ValueError('Internal Threads worker arguments are invalid')
        download_threads(sys.argv[2], sys.argv[3], sys.argv[4])
    except Exception as exc:
        print('ERROR: ' + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
