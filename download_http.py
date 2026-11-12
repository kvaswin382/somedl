"""Bounded HTTP transfers, content typing and validated range resume.

A refreshed URL is NOT proof that it identifies the same bytes. Resume requires a
strong entity validator and matching length; otherwise retain the old partial and
restart. Cookies are supplied by CookieJar, never by a global Cookie header.
"""
import hashlib
import http.cookiejar
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from media import validate_url
from publishing import filename

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
MANIFEST_TYPES = {'application/vnd.apple.mpegurl', 'application/x-mpegurl', 'application/dash+xml'}


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w') as out:
        os.chmod(temp, 0o600)
        json.dump(value, out); out.flush(); os.fsync(out.fileno())
    os.replace(temp, path)


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            for key in list(result.headers):
                if key.lower() in ('authorization', 'cookie', 'origin', 'proxy-authorization'):
                    del result.headers[key]
        return result


def opener(jar=None):
    # Ignore host proxy environment for local destination validation consistency.
    if jar is None:jar=http.cookiejar.MozillaCookieJar()
    # Match browser host-only semantics. Python's legacy default otherwise sends
    # a host-only session cookie to subdomains as well.
    jar.set_policy(http.cookiejar.DefaultCookiePolicy(strict_ns_domain=http.cookiejar.DefaultCookiePolicy.DomainStrictNonDomain))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirect(),
        urllib.request.HTTPCookieProcessor(jar))


def request(client, url, headers=None, timeout=25):
    validate_url(url)
    h = {'User-Agent': UA, 'Accept': '*/*', 'Accept-Encoding': 'identity'}
    for key, value in (headers or {}).items():
        if key.lower() in ('cookie', 'host', 'proxy-authorization'):
            continue
        if '\r' in str(value) or '\n' in str(value):
            raise ValueError('Invalid request header')
        h[key] = value
    return client.open(urllib.request.Request(url, headers=h), timeout=timeout)


def identify(data, content_type='', url='', disposition=''):
    """Return (kind, extension). Actual magic wins over URL and requested filename."""
    mime = content_type.split(';')[0].lower().strip()
    if data.startswith(b'\xff\xd8\xff'): return 'image', '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image', '.png'
    if data[:6] in (b'GIF87a', b'GIF89a'): return 'image', '.gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return 'image', '.webp'
    if data[4:8] == b'ftyp':
        if data[8:12] in (b'avif', b'avis'): return 'image', '.avif'
        if data[8:12] in (b'heic', b'heix', b'mif1'): return 'image', '.heic'
        return ('audio', '.m4a') if mime.startswith('audio/') else ('video', '.mp4')
    if data.startswith(b'\x1a\x45\xdf\xa3'):return 'video', '.webm' if b'webm' in data else '.mkv'
    if data.startswith(b'ID3'):return 'audio', '.mp3'
    if data.startswith(b'fLaC'):return 'audio', '.flac'
    if data[:4]==b'RIFF' and data[8:12]==b'WAVE':return 'audio', '.wav'
    head = data.lstrip(b'\xef\xbb\xbf \r\n\t').lower()
    if head.startswith(b'#extm3u') or mime in MANIFEST_TYPES or re.search(br'<mpd[\s>]',head[:4096]):
        return 'manifest', '.m3u8' if head.startswith(b'#extm3u') or 'mpegurl' in mime else '.mpd'
    if re.search(br'<(?:!doctype html|html|head|body)[\s>]',head[:4096]) or mime in ('text/html','application/xhtml+xml'):
        return 'page', '.html'
    if mime.startswith('image/'):
        return 'image', { 'image/jpeg':'.jpg', 'image/svg+xml':'.svg' }.get(mime, mimetypes.guess_extension(mime) or '.img')
    if mime.startswith(('video/', 'audio/')):
        return mime.split('/')[0], mimetypes.guess_extension(mime) or '.bin'
    if data.startswith(b'%PDF'): return 'file', '.pdf'
    if data.startswith(b'PK\x03\x04'): return 'file', '.zip'
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    if mime == 'application/octet-stream' or 'attachment' in disposition.lower():
        return 'file', suffix if re.fullmatch(r'\.[a-z0-9]{1,10}', suffix) else '.bin'
    if mime.startswith('text/') or mime in ('application/json','application/xml'):
        # Explicit text files are downloads; API/page responses are discovery inputs.
        if suffix in ('.txt','.csv','.json','.xml','.srt','.vtt'):return 'file', suffix
        return 'page', suffix
    return 'file', suffix if re.fullmatch(r'\.[a-z0-9]{1,10}',suffix) else '.bin'


def inspect(client, url, headers=None, timeout=25):
    with request(client, url, headers, timeout) as response:
        data = response.read(4096)
        kind, ext = identify(data, response.headers.get('Content-Type',''), response.url,
                             response.headers.get('Content-Disposition',''))
        safe_headers=dict(headers or {})
        if urllib.parse.urlsplit(url).netloc!=urllib.parse.urlsplit(response.url).netloc:
            safe_headers={k:v for k,v in safe_headers.items() if k.lower() not in ('authorization','cookie','origin')}
        result = {'url':response.url, 'kind':kind, 'ext':ext, 'headers':safe_headers,
                  'mime':response.headers.get('Content-Type',''), 'prefix':data}
        if kind == 'page':
            data += response.read(2*1024*1024 + 1 - len(data))
            if len(data)>2*1024*1024:raise ValueError('Page exceeds discovery limit of 2 MiB')
            result['html']=data.decode('utf-8','replace')
        return result


def output_register(dest, name):
    path = Path(dest)/'.outputs.json'
    names = json.loads(path.read_text()) if path.exists() else []
    if name not in names:names.append(name)
    atomic_json(path, names)


def transfer(client, candidate, dest, name='Download', rate_kib=0, timeout=30, identity='primary', _restart=False):
    """One request per attempt. Keep partial + validator across process restarts."""
    dest = Path(dest)
    key = hashlib.sha256(identity.encode()).hexdigest()[:20]
    partial = dest/('.http-'+key+'.part')
    meta_path = dest/('.http-'+key+'.json')
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    headers = dict(candidate.get('headers') or {})
    offset = partial.stat().st_size if partial.exists() else 0
    tag = meta.get('etag','')
    can_resume = bool(not _restart and offset and tag and not tag.startswith('W/') and meta.get('total'))
    if can_resume:
        headers.update({'Range':f'bytes={offset}-', 'If-Range':tag})
    elif offset:
        print('ZEDPROFILE Server provided no strong resume validator; restarting this file safely.',flush=True)
        offset = 0
    try:
        response = request(client,candidate['url'],headers,timeout)
    except urllib.error.HTTPError as exc:
        if exc.code==416 and can_resume:
            # Never declare a partial complete just because the server rejects a range.
            headers.pop('Range',None);headers.pop('If-Range',None);offset=0
            response=request(client,candidate['url'],headers,timeout)
        else:raise
    with response:
        status=response.status
        etag=response.headers.get('ETag','')
        length=int(response.headers.get('Content-Length') or 0)
        if status==206:
            match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
            valid=(can_resume and match and int(match[1])==offset and etag==tag and int(match[3])==meta.get('total')
                   and int(match[2])==int(match[3])-1 and (not length or length==int(match[2])-offset+1))
            if not valid:
                response.close()
                if _restart:raise ValueError('Unsafe resume refused: server returned an invalid range for a full request')
                print('ZEDPROFILE File identity or range changed; restarting safely without appending.',flush=True)
                return transfer(client,candidate,dest,name,rate_kib,timeout,identity,_restart=True)
            total=int(match[3]);kind,ext=meta['kind'],meta['ext'];first=b''
        elif status==200:
            offset=0;total=length
            first=response.read(4096)
            kind,ext=identify(first,response.headers.get('Content-Type',''),response.url,response.headers.get('Content-Disposition',''))
        else:raise ValueError(f'Unexpected transfer HTTP {status}')
        if kind in ('page','manifest'):
            raise ValueError('Transfer returned a page or playlist instead of file bytes; source needs re-extraction')
        atomic_json(meta_path,{'etag':etag,'total':total,'kind':kind,'ext':ext})
        target=dest/(filename(Path(name).stem if Path(name).suffix else name)+ext)
        done=offset;received=0;tick=time.monotonic();start=tick;reported=0
        with partial.open('ab' if offset else 'wb') as out:
            block=first
            while True:
                if block:
                    out.write(block);done+=len(block);received+=len(block)
                    if rate_kib:time.sleep(max(0,received/(rate_kib*1024)-(time.monotonic()-start)))
                    if time.monotonic()-tick>=1:
                        print('ZEDBYTES '+str(received-reported),flush=True);reported=received
                        speed=received/max(.001,time.monotonic()-start)
                        print('ZEDPROG '+json.dumps({'downloaded_bytes':done,'total_bytes':total,'speed':speed,
                              'eta':max(0,(total-done)/speed) if total else None,'filename':target.name}),flush=True)
                        tick=time.monotonic()
                block=response.read(256*1024)
                if not block:break
            out.flush();os.fsync(out.fileno())
        print('ZEDBYTES '+str(received-reported),flush=True)
        if not done or (total and done!=total):raise ValueError(f'Incomplete transfer: received {done} of {total or "unknown"} bytes; partial retained')
        os.replace(partial,target);meta_path.unlink(missing_ok=True)
        output_register(dest,target.name)
        print('ZEDPROFILE Saved '+kind+' as '+target.name,flush=True)
        return target
