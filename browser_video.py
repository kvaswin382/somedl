"""Optional single-page media discovery after a normal extractor fails."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from media import validate_url, normalize_url
from page_media import discover, choose, USER_AGENT
import platforms
import profiles

EXTENSIONS=('.m3u8','.mpd','.mp4','.webm','.m4v','.mov')

def media_kind(url,content_type=''):
    path=urlsplit(url).path.lower();content_type=content_type.lower().split(';')[0]
    if path.endswith(('.ts','.m4s','.vtt')):return ''
    if path.endswith(('.m3u8','.mpd')) or content_type in ('application/vnd.apple.mpegurl','application/x-mpegurl','application/dash+xml'):return 'manifest'
    if path.endswith(EXTENSIONS) or content_type.startswith('video/'):return 'video'
    return ''

def pick(candidates,active,quality='best',mp4=False):
    dom=[c for c in candidates if c.get('dom') and c.get('height')]
    if dom and len({c.get('group') for c in dom})==1:
        try:return choose(dom,quality,mp4)
        except ValueError:pass
    exact=[c for c in candidates if c['url'] in active]
    if len(exact)==1:return exact[0]
    masters=[c for c in candidates if c.get('master')]
    if len(masters)==1:return masters[0]
    if candidates and all(c.get('dom') for c in candidates):return choose(candidates,quality,mp4)
    manifests=[c for c in candidates if c['kind']=='manifest']
    if len(manifests)==1:return manifests[0]
    if len(candidates)==1:return candidates[0]
    raise ValueError('No unambiguous playable media found. Multiple players/ads or a protected player may require a supported site extractor.')

def available_memory():
    try:
        values={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line and len(line.split())>=2 and line.split()[1].isdigit()}
        return values.get('MemAvailable',0)
    except OSError:return 0

def detect(url,cookie,quality='best',mp4=False):
    from playwright.sync_api import sync_playwright
    if available_memory()<450*1024**2:raise ValueError('Browser fallback postponed: less than 450 MiB available RAM. Close other tasks and retry.')
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(profiles.BROWSERS)
    candidates={};active=set();title='';validated=set()
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,chromium_sandbox=True,timeout=30000,args=['--disable-dev-shm-usage','--disable-background-networking','--disable-extensions','--disable-quic','--renderer-process-limit=2','--js-flags=--max-old-space-size=128','--autoplay-policy=no-user-gesture-required'])
        try:
            context=browser.new_context(accept_downloads=False,service_workers='block',viewport={'width':960,'height':540},user_agent=USER_AGENT,locale='en-US')
            context.set_default_timeout(2000)
            if cookie:
                jar=platforms.load_jar(Path(cookie))
                items=[{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path or '/','secure':c.secure,'httpOnly':c.has_nonstandard_attr('HttpOnly'),**({'expires':c.expires} if c.expires and c.expires>time.time() else {})} for c in jar if not c.is_expired()]
                if items:context.add_cookies(items)
            def route(request_route):
                req=request_route.request
                try:
                    if req.resource_type in ('image','font') or urlsplit(req.url).scheme not in ('http','https'):return request_route.abort()
                    parsed=urlsplit(req.url);origin=(parsed.scheme,parsed.hostname,parsed.port)
                    if origin not in validated:validate_url(req.url);validated.add(origin)
                    if req.resource_type=='media' and media_kind(req.url)=='video':
                        headers=req.all_headers()
                        if len(candidates)<40:candidates[req.url]={'url':req.url,'kind':'video','headers':{k:headers[k] for k in ('referer','origin','user-agent') if k in headers}}
                        return request_route.abort()  # Capture the URL without buffering the movie in Chromium.
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
                        item={'url':res.url,'kind':kind,'headers':{k:headers[k] for k in ('referer','origin','user-agent') if k in headers}}
                        if kind=='manifest' and 0<int(res.headers.get('content-length','0') or 0)<512000:
                            try:item['master']='#EXT-X-STREAM-INF' in res.text()[:512000]
                            except Exception:pass
                        candidates[res.url]={**candidates.get(res.url,{}),**item}
                except Exception:pass
            page.on('response',response)
            deadline=time.monotonic()+75
            print('ZEDPROFILE Browser launched; loading the player page…',flush=True)
            try:page.goto(normalize_url(url),wait_until='domcontentloaded',timeout=35000)
            except Exception as exc:
                if 'Timeout' not in type(exc).__name__:raise
                print('ZEDPROFILE Page load timed out; inspecting the player already loaded…',flush=True)
            title=page.title()
            for _ in range(20):
                if time.monotonic()>=deadline:break
                if available_memory()<180*1024**2:raise ValueError('Browser stopped because available RAM is low')
                text=page.locator('body').inner_text(timeout=2000)[:12000].lower() if page.locator('body').count() else ''
                if any(t in text for t in ('verify you are human','checking your browser','unusual traffic','complete the captcha','verify your age')):
                    raise ValueError('Website requires verification; browser fallback stopped')
                for frame in page.frames[:12]:
                    if time.monotonic()>=deadline:break
                    try:
                        sources=frame.locator('video').evaluate_all("els=>els.filter(v=>v.getBoundingClientRect().width>100).sort((a,b)=>b.clientWidth*b.clientHeight-a.clientWidth*a.clientHeight).slice(0,1).flatMap(v=>{v.muted=true;v.play().catch(()=>{});return [{url:v.currentSrc||v.src,active:true},...Array.from(v.querySelectorAll('source')).map(s=>({url:s.src,height:s.getAttribute('size')||s.getAttribute('label')||s.getAttribute('res')||0}))]})")
                        for source in sources:
                            if source.get('active'):active.add(source['url'])
                            if media_kind(source['url']):
                                value={'url':source['url'],'kind':media_kind(source['url']),'headers':{'referer':frame.url,'user-agent':USER_AGENT},'group':frame.url,'dom':True,'height':0}
                                height=re.search(r'(\d{3,4})',str(source.get('height',0)))
                                if height:value['height']=int(height[1])
                                candidates[source['url']]={**value,**candidates.get(source['url'],{}),'dom':True,'group':frame.url,'height':max(value['height'],candidates.get(source['url'],{}).get('height',0))}
                        if _ in (0,3,7):
                            buttons=frame.locator('button[aria-label*="Play" i],button[title*="Play" i],.vjs-big-play-button,.plyr__control--overlaid,.jw-icon-display,.fp-play,.play-button,#play')
                            if buttons.count() and buttons.first.is_visible():buttons.first.click(timeout=1000)
                    except Exception:pass
                if candidates and _>=5:
                    try:pick(list(candidates.values()),active,quality,mp4);break
                    except ValueError:pass
                page.wait_for_timeout(1000)
            result=pick(list(candidates.values()),active,quality,mp4)
            # Cookies are domain-scoped; do not forward a page's raw Cookie/Authorization header to a CDN.
            import http.cookiejar
            jar=http.cookiejar.MozillaCookieJar()
            for c in context.cookies():
                jar.set_cookie(http.cookiejar.Cookie(0,c['name'],c['value'],None,False,c['domain'],c['domain'].startswith('.'),c['domain'].startswith('.'),c['path'],True,c['secure'],int(c['expires']) if c.get('expires',-1)>0 else None,c.get('expires',-1)<=0,None,None,{'HttpOnly':None} if c.get('httpOnly') else {},False))
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
    force=spec.get('force',False)
    print('ZEDENGINE '+('browser' if force else 'video'),flush=True)
    if not force:
        code,log=execute(spec['args'])
        if not code:return 0
        low=log.lower()
        if not spec['enabled']:return code
        if any(x in low for x in ('captcha','drm','login required','sign in','verify you are human','verify your age','requested format is not available','429','too many requests')):return code
        if not any(x in low for x in ('unsupported url','unable to extract','no video could be found','no video formats','does not contain a video','authorization failed','unable to download webpage','http error 403','no downloadable media','no supported media')):return code
        if any(p.suffix.lower() in ('.mp4','.mkv','.webm','.mp3','.m4a') for p in Path(spec['dest']).iterdir()):return code
    args=spec['args'];fmt=args[args.index('-f')+1] if '-f' in args else ''
    ceiling=re.search(r'height<=(\d+)',fmt);quality=ceiling[1] if ceiling else 'best';mp4='[ext=mp4]' in fmt
    detected=None
    if not force:
        print('ZEDENGINE page',flush=True)
        print('ZEDPROFILE Checking page sources and embedded players…',flush=True)
        try:detected=discover(normalize_url(spec['url']),spec.get('cookie',''),quality,mp4)
        except Exception as exc:print('Page extraction: '+str(exc),flush=True)
    detected_from_page=detected is not None
    if detected is None:
        if not profiles.browser_ready():raise ValueError('Browser engine is not installed. Run sudo bash /opt/zedair/install-browser.sh and restart zedair, then retry.')
        print('ZEDENGINE browser',flush=True)
        print('ZEDPROFILE Detecting playback in the browser (up to 75 seconds)…',flush=True)
        detected=detect(spec['url'],spec.get('cookie'),quality,mp4)
    found,title,jar=detected
    result=transfer(spec,found,title,jar)
    if result and not force and not any(p.suffix.lower() in ('.mp4','.webm','.mkv') for p in Path(spec['dest']).iterdir()) and profiles.browser_ready():
        # One additional browser attempt, never a recursive extraction loop.
        if detected_from_page:
            print('ZEDENGINE browser',flush=True)
            print('ZEDPROFILE Page source failed; trying playback detection once…',flush=True)
            found,title,jar=detect(spec['url'],spec.get('cookie'),quality,mp4)
            return transfer(spec,found,title,jar)
    return result


def transfer(spec,found,title,jar):
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
        print('ZEDPROFILE Playback URL detected; downloading media…',flush=True)
        return execute(args)[0]
    finally:temporary.unlink(missing_ok=True)

if __name__=='__main__':
    try:raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
    except Exception as exc:print('ERROR Browser fallback: '+str(exc),flush=True);raise SystemExit(1)
