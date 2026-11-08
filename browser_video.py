"""Optional single-page media discovery after a normal extractor fails."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from media import validate_url
import platforms
import profiles

EXTENSIONS=('.m3u8','.mpd','.mp4','.webm','.m4v','.mov')

def media_kind(url,content_type=''):
    path=urlsplit(url).path.lower();content_type=content_type.lower().split(';')[0]
    if path.endswith(('.ts','.m4s','.vtt')):return ''
    if path.endswith(('.m3u8','.mpd')) or content_type in ('application/vnd.apple.mpegurl','application/x-mpegurl','application/dash+xml'):return 'manifest'
    if path.endswith(EXTENSIONS) or content_type.startswith('video/'):return 'video'
    return ''

def pick(candidates,active):
    exact=[c for c in candidates if c['url'] in active]
    if len(exact)==1:return exact[0]
    manifests=[c for c in candidates if c['kind']=='manifest']
    if len(manifests)==1:return manifests[0]
    if len(candidates)==1:return candidates[0]
    raise ValueError('No unambiguous playable media found. Multiple players/ads or a protected player may require a supported site extractor.')

def available_memory():
    try:
        values={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line and len(line.split())>=2 and line.split()[1].isdigit()}
        return values.get('MemAvailable',0)
    except OSError:return 0

def detect(url,cookie):
    from playwright.sync_api import sync_playwright
    if available_memory()<450*1024**2:raise ValueError('Browser fallback postponed: less than 450 MiB available RAM. Close other tasks and retry.')
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(profiles.BROWSERS)
    candidates={};active=set();title='';validated=set()
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,chromium_sandbox=True,timeout=30000,args=['--disable-dev-shm-usage','--disable-background-networking','--disable-extensions','--disable-quic','--renderer-process-limit=2','--js-flags=--max-old-space-size=128','--autoplay-policy=no-user-gesture-required'])
        try:
            context=browser.new_context(accept_downloads=False,service_workers='block',viewport={'width':960,'height':540})
            context.set_default_timeout(2000)
            if cookie:
                jar=platforms.load_jar(Path(cookie))
                items=[{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path or '/','secure':c.secure,**({'expires':c.expires} if c.expires and c.expires>time.time() else {})} for c in jar if not c.is_expired()]
                if items:context.add_cookies(items)
            def route(request_route):
                req=request_route.request
                try:
                    if req.resource_type in ('image','font') or urlsplit(req.url).scheme not in ('http','https'):return request_route.abort()
                    parsed=urlsplit(req.url);origin=(parsed.scheme,parsed.hostname,parsed.port)
                    if origin not in validated:validate_url(req.url);validated.add(origin)
                    request_route.continue_()
                except Exception:request_route.abort()
            context.route('**/*',route)
            context.route_web_socket('**/*',lambda socket:socket.close())
            page=context.new_page()
            context.on('page',lambda other:other.close() if other!=page else None)
            def response(res):
                try:
                    kind=media_kind(res.url,res.headers.get('content-type',''))
                    if kind and res.status in (200,206) and len(candidates)<40:
                        request=res.request;headers=request.all_headers()
                        candidates[res.url]={'url':res.url,'kind':kind,'headers':{k:headers[k] for k in ('referer','origin','user-agent') if k in headers}}
                except Exception:pass
            page.on('response',response)
            deadline=time.monotonic()+75
            page.goto(url,wait_until='domcontentloaded',timeout=45000)
            title=page.title()
            for _ in range(20):
                if time.monotonic()>=deadline:break
                if available_memory()<180*1024**2:raise ValueError('Browser stopped because available RAM is low')
                text=page.locator('body').inner_text(timeout=3000)[:12000].lower()
                if any(t in text for t in ('verify you are human','checking your browser','unusual traffic','captcha')):
                    raise ValueError('Website requires verification; browser fallback stopped')
                for frame in page.frames[:12]:
                    if time.monotonic()>=deadline:break
                    try:
                        active.update(frame.locator('video').evaluate_all("els=>els.map(v=>{v.muted=true;v.play().catch(()=>{});return v.currentSrc||v.src}).filter(Boolean)"))
                        if _==0:
                            buttons=frame.locator('button[aria-label*="Play"],.vjs-big-play-button,.plyr__control--overlaid,.jw-icon-display')
                            if buttons.count() and buttons.first.is_visible():buttons.first.click(timeout=1000)
                    except Exception:pass
                if candidates and _>=3:break
                page.wait_for_timeout(1000)
            result=pick(list(candidates.values()),active)
            # Cookies are domain-scoped; do not forward a page's raw Cookie/Authorization header to a CDN.
            import http.cookiejar
            jar=http.cookiejar.MozillaCookieJar()
            for c in context.cookies():
                jar.set_cookie(http.cookiejar.Cookie(0,c['name'],c['value'],None,False,c['domain'],c['domain'].startswith('.'),c['domain'].startswith('.'),c['path'],True,c['secure'],int(c['expires']) if c.get('expires',-1)>0 else None,c.get('expires',-1)<=0,None,None,{},False))
            return result,title,jar
        finally:browser.close()

def execute(args):
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    tail=[]
    for line in process.stdout:
        print(line,end='',flush=True);tail.append(line)
        tail=tail[-60:]
    return process.wait(),''.join(tail)

def run(spec):
    code,log=execute(spec['args'])
    if not code:return 0
    low=log.lower()
    if not spec['enabled'] or not any(x in low for x in ('unsupported url','unable to extract','no video could be found','no video formats','does not contain a video')):return code
    if any(x in low for x in ('captcha','drm','login required','sign in','verify you are human')):return code
    if any(p.suffix.lower() in ('.mp4','.mkv','.webm','.mp3','.m4a') for p in Path(spec['dest']).iterdir()):return code
    if not profiles.browser_ready():raise ValueError('Install optional browser detection: sudo bash /opt/zedair/install-browser.sh')
    print('ZEDPROFILE Detecting playback media in one browser page (up to 75 seconds)…',flush=True)
    found,title,jar=detect(spec['url'],spec.get('cookie'))
    validate_url(found['url'])
    # Use the same quality/container controls on the detected manifest/direct URL.
    args=spec['args'][:];args[-1]=found['url']
    if '--cookies' in args:
        index=args.index('--cookies');del args[index:index+2]
    temporary=Path(spec['dest'])/'.browser-cookies.txt'
    try:
        temporary.touch(mode=0o600);temporary.chmod(0o600);jar.save(str(temporary),ignore_discard=True,ignore_expires=True)
        extra=['--cookies',str(temporary)]
        for k,v in found['headers'].items():extra+=['--add-header',k+':'+v]
        if title:extra+=['--replace-in-metadata','title','(?s)^.*$',title.replace('\\','\\\\')]
        index=args.index('--');args[index:index]=extra
        print('ZEDPROFILE Playback URL detected. Browser closed; downloading media…',flush=True)
        return execute(args)[0]
    finally:temporary.unlink(missing_ok=True)

if __name__=='__main__':
    try:raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
    except Exception as exc:print('ERROR Browser fallback: '+str(exc),flush=True);raise SystemExit(1)
