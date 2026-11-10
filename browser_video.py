"""Optional single-page media discovery after a normal extractor fails."""
import json
import os
import re
import signal
import subprocess
import sys
import threading
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
    posts=[c for c in candidates if c.get('kind')=='reddit_post']
    if posts:return posts[0]
    embeds=[c for c in candidates if c.get('kind')=='embed']
    if embeds:return embeds[0]
    hls_masters=[c for c in candidates if 'hlsplaylist.m3u8' in c['url'].lower()]
    if hls_masters:return hls_masters[0]
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
    galleries=[c for c in candidates if c.get('kind')=='gallery']
    if galleries:return galleries[0]
    if len(candidates)==1:return candidates[0]
    raise ValueError('No unambiguous playable media found. Multiple players/ads or a protected player may require a supported site extractor.')

def available_memory():
    try:
        p=Path('/proc/meminfo')
        if not p.is_file():return 1024*1024*1024
        values={line.split(':')[0]:int(line.split()[1])*1024 for line in p.read_text().splitlines() if ':' in line and len(line.split())>=2 and line.split()[1].isdigit()}
        return values.get('MemAvailable',0)
    except OSError:return 1024*1024*1024

class TelegramLog:
    """Best-effort Telegram text/snapshots. Deliveries run in threads that are joined before exit,
    so a slow Telegram connection never stalls detection and never gets cut off by process exit."""
    def __init__(self,telegram):
        telegram=telegram or {}
        self.token=str(telegram.get('bot_token','')).strip()
        self.recipients=[str(c).strip() for c in (telegram.get('chat_id'),telegram.get('admin_id')) if str(c or '').strip()]
        self.enabled=bool(telegram.get('snapshots',False)) and bool(self.token) and bool(self.recipients)
        self.pending=[]
        self.notifier=None
        if bool(telegram.get('snapshots',False)) and not self.enabled:
            print('ZEDPROFILE Telegram snapshots are on but the bot token or chat/admin ID is empty; nothing will be sent.',flush=True)
        if self.enabled:
            try:
                import telegram_notifier
                self.notifier=telegram_notifier
            except Exception as exc:
                self.enabled=False
                print(f'Telegram snapshot unavailable: {exc}. Is telegram_notifier.py installed in /opt/zedair?',flush=True)
    def _deliver(self,fn):
        worker=threading.Thread(target=fn,daemon=True);worker.start();self.pending.append(worker)
    def text(self,message):
        if not self.enabled:return
        def send():
            result=self.notifier.send_message(self.token,self.recipients,message)
            if not result.get('ok'):print('Telegram message failed: '+str(result.get('error',''))[:300],flush=True)
            for error in result.get('errors') or []:print('Telegram message failed: '+str(error)[:300],flush=True)
        self._deliver(send)
    def snapshot(self,page,caption):
        if not self.enabled:return
        try:shot=page.screenshot(type='jpeg',quality=65,timeout=8000,animations='disabled')
        except Exception as exc:
            print(f'Telegram snapshot: could not capture the page ({str(exc)[:120]})',flush=True);shot=None
        if shot:self._deliver(lambda:self.notifier.send_snapshot(self.token,self.recipients,shot,caption=caption))
        else:self.text(caption+'\n(screenshot unavailable)')
    def flush(self,seconds=60):
        deadline=time.monotonic()+seconds
        for worker in self.pending:worker.join(max(0,deadline-time.monotonic()))
        self.pending=[w for w in self.pending if w.is_alive()]

def arm_watchdog(seconds,on_fire=None):
    """Hard stop: a wedged browser page can block Playwright calls forever (they have no timeout)."""
    def fire():
        print('ERROR Browser fallback: the browser page stopped responding; stopped by watchdog',flush=True)
        if on_fire:
            try:
                notify=threading.Thread(target=on_fire,daemon=True);notify.start();notify.join(20)
            except Exception:pass
        try:
            if os.getpid()==os.getpgrp():os.killpg(os.getpgrp(),signal.SIGKILL)
        except Exception:pass
        os._exit(3)
    timer=threading.Timer(seconds,fire);timer.daemon=True;timer.start()
    return timer

def detect(url,cookie,quality='best',mp4=False,telegram=None):
    tg=TelegramLog(telegram)
    def on_fire():
        if tg.enabled:tg.notifier.send_message(tg.token,tg.recipients,f'Zedair browser engine timed out (no response for 4 minutes).\nURL: {url}')
    watchdog=arm_watchdog(240,on_fire)
    try:return _detect(url,cookie,quality,mp4,tg)
    finally:
        watchdog.cancel()
        tg.flush()

def reddit_post_from_page(page):
    """Read the post JSON with the browser's own session (cookies + browser network stack)."""
    import reddit_media
    match=re.search(r'/comments/([a-z0-9]+)',urlsplit(page.url).path)
    if not match:return None
    data=page.evaluate("""async (u) => {
        const ctl=new AbortController();const timer=setTimeout(()=>ctl.abort(),15000);
        try{const r=await fetch(u,{credentials:'include',signal:ctl.signal,headers:{'Accept':'application/json'}});
            return {status:r.status,text:r.ok?(await r.text()).slice(0,8000000):''};}
        catch(e){return {status:0,text:String(e)};}
        finally{clearTimeout(timer);}
    }""",f'/comments/{match[1]}.json?raw_json=1&limit=1')
    if not data or data.get('status')!=200:
        print(f"Reddit post JSON in browser: HTTP {data.get('status') if data else '?'}",flush=True);return None
    post=reddit_media.find_post(json.loads(data['text']))
    if not post:return None
    return reddit_media.classify(post)

def _detect(url,cookie,quality='best',mp4=False,tg=None):
    from playwright.sync_api import sync_playwright
    tg=tg if isinstance(tg,TelegramLog) else TelegramLog(tg)
    if available_memory()<450*1024**2:raise ValueError('Browser fallback postponed: less than 450 MiB available RAM. Close other tasks and retry.')
    if profiles.BROWSERS.is_dir():os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(profiles.BROWSERS)
    candidates={};active=set();title='';validated=set()
    is_reddit=platforms.platform_for(url)=='reddit'
    send_tg_snapshot=tg.snapshot
    tg.text(f'Zedair browser engine started.\nURL: {url}')
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,chromium_sandbox=True,timeout=30000,args=['--disable-dev-shm-usage','--disable-background-networking','--disable-extensions','--disable-quic','--renderer-process-limit=2','--js-flags=--max-old-space-size=384','--autoplay-policy=no-user-gesture-required'])
        page=None
        try:
            context=browser.new_context(accept_downloads=False,service_workers='block',viewport={'width':960,'height':540},user_agent=USER_AGENT,locale='en-US')
            context.set_default_timeout(3000)
            if cookie:
                jar=platforms.load_jar(Path(cookie))
                items=[{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path or '/','secure':c.secure,'httpOnly':c.has_nonstandard_attr('HttpOnly'),**({'expires':c.expires} if c.expires and c.expires>time.time() else {})} for c in jar if not c.is_expired()]
                if items:context.add_cookies(items)
            if is_reddit:
                try:
                    context.add_cookies([
                        {'name':'over18','value':'1','domain':'.reddit.com','path':'/'},
                        {'name':'over18','value':'1','domain':'www.reddit.com','path':'/'},
                        {'name':'edgebucket','value':'1','domain':'.reddit.com','path':'/'}
                    ])
                except Exception:pass
            def route(request_route):
                req=request_route.request
                try:
                    if req.resource_type=='font' or urlsplit(req.url).scheme not in ('http','https'):return request_route.abort()
                    if not is_reddit and req.resource_type=='image':return request_route.abort()
                    parsed=urlsplit(req.url);origin=(parsed.scheme,parsed.hostname,parsed.port)
                    if origin not in validated:validate_url(req.url,dns=False);validated.add(origin)
                    if req.resource_type=='media' and media_kind(req.url)=='video':
                        headers=req.all_headers()
                        if len(candidates)<40:candidates[req.url]={'url':req.url,'kind':'video','headers':{k:headers[k] for k in ('referer','origin','user-agent') if k in headers}}
                        return request_route.abort()  # Capture the URL without buffering the movie in Chromium.
                    request_route.continue_()
                except Exception:request_route.abort()
            context.route('**/*',route)
            context.route_web_socket('**/*',lambda socket:socket.close())
            page=context.new_page()
            page.on('dialog',lambda dialog:dialog.dismiss())
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
            send_tg_snapshot(page,f'Browser page opened: {urlsplit(url).netloc}\nTitle: {title}')
            if is_reddit:
                try:
                    info=reddit_post_from_page(page)
                    if info:
                        print(f"ZEDPROFILE Reddit post read in the browser session: {info['kind']}",flush=True)
                        candidates['__reddit_post__']={'url':page.url,'kind':'reddit_post','info':info,'headers':{}}
                        title=info.get('title') or title
                except Exception as exc:print('Reddit post JSON in browser: '+str(exc)[:200],flush=True)
            for _ in range(0 if '__reddit_post__' in candidates else 25):
                if time.monotonic()>=deadline:break
                if _==12:send_tg_snapshot(page,f'Browser still searching for playable media…\nTitle: {title}')
                if available_memory()<180*1024**2:raise ValueError('Browser stopped because available RAM is low')
                text=page.locator('body').inner_text(timeout=2000)[:12000].lower() if page.locator('body').count() else ''
                check_tokens=('verify you are human','unusual traffic','complete the captcha') if is_reddit else ('verify you are human','checking your browser','unusual traffic','complete the captcha')
                if any(t in text for t in check_tokens):
                    raise ValueError('Website requires verification; browser fallback stopped')
                if is_reddit:
                    for sel in (
                        'button:has-text("Yes")',
                        'button:has-text("I\'m over 18")',
                        'button:has-text("I am 18 or older")',
                        'button:has-text("Continue")',
                        'button:has-text("View in browser")',
                        'button:has-text("Continue in browser")',
                        'button:has-text("Click to see NSFW")',
                        'button:has-text("View NSFW")',
                        'button:has-text("See NSFW")',
                        'button:has-text("Show NSFW")',
                        'button:has-text("I understand")',
                        'shreddit-action-row button[slot="action-item"]',
                        '[data-testid="nsfw-warning-accept"]',
                    ):
                        try:
                            loc=page.locator(sel)
                            if loc.count() and loc.first.is_visible():loc.first.click(timeout=800);page.wait_for_timeout(300)
                        except Exception:pass
                    try:
                        page.evaluate("""() => {
                            document.querySelectorAll('[data-testid="nsfw-warning-accept"], button').forEach(b => {
                                const txt = (b.textContent || '').trim().toLowerCase();
                                if (txt.includes('over 18') || txt.includes('view nsfw') || txt.includes('see nsfw') || txt.includes('show nsfw') || txt.includes('continue') || txt.includes('i understand')) {
                                    try { b.click(); } catch(e) {}
                                }
                            });
                            document.querySelectorAll('*').forEach(el => {
                                if (el.style && el.style.filter && el.style.filter.includes('blur')) el.style.filter = 'none';
                            });
                        }""")
                    except Exception:pass
                    try:
                        sp_info = page.evaluate("""() => {
                            const sp = document.querySelector('shreddit-post');
                            if (!sp) return null;
                            return {
                                title: sp.getAttribute('post-title'),
                                href: sp.getAttribute('content-href'),
                                domain: (sp.getAttribute('domain') || '').toLowerCase(),
                                type: (sp.getAttribute('post-type') || '').toLowerCase()
                            };
                        }""")
                        if sp_info:
                            if sp_info.get('title'):
                                title = sp_info['title'].strip()
                            href = sp_info.get('href')
                            domain = sp_info.get('domain', '')
                            if href:
                                if 'redgifs.com' in domain or 'redgifs.com' in href:
                                    m = re.search(r'redgifs\.com/(?:ifr|watch)/([a-zA-Z0-9]+)', href, re.I)
                                    if m:
                                        w_url = f'https://www.redgifs.com/watch/{m.group(1)}'
                                        candidates[w_url] = {'url': w_url, 'kind': 'embed', 'headers': {'referer': 'https://www.reddit.com/', 'user-agent': USER_AGENT}}
                                elif 'imgur.com' in domain or 'streamable.com' in domain:
                                    candidates[href] = {'url': href, 'kind': 'embed', 'headers': {'referer': 'https://www.reddit.com/', 'user-agent': USER_AGENT}}
                                elif href.endswith(('.gif', '.jpg', '.jpeg', '.png', '.webp', '.avif')):
                                    candidates['__gallery__'] = {'url': href, 'kind': 'gallery', 'urls': [href], 'headers': {'referer': 'https://www.reddit.com/', 'user-agent': USER_AGENT}}
                                elif href.endswith(('.mp4', '.webm', '.mov', '.m3u8', '.mpd')) or 'v.redd.it' in domain:
                                    candidates[href] = {'url': href, 'kind': media_kind(href) or 'video', 'headers': {'referer': 'https://www.reddit.com/', 'user-agent': USER_AGENT}}
                    except Exception:pass
                for frame in page.frames[:12]:
                    if time.monotonic()>=deadline:break
                    rg_frame=re.search(r'https?://(?:www\.)?redgifs\.com/(?:ifr|watch)/([a-zA-Z0-9]+)',frame.url,re.I)
                    if rg_frame:
                        w_url=f'https://www.redgifs.com/watch/{rg_frame.group(1)}'
                        candidates[w_url]={'url':w_url,'kind':'embed','headers':{'referer':'https://www.reddit.com/','user-agent':USER_AGENT}}
                    try:
                        embeds=frame.evaluate("""() => {
                            const found=[];
                            for(const el of document.querySelectorAll('iframe[src], a[href], shreddit-embed[src]')){
                                const u=el.src||el.href||el.getAttribute('src');
                                if(u&&/(?:redgifs\\.com\\/(?:ifr|watch)\\/|imgur\\.com\\/(?:a\\/|gallery\\/)?[\\w]+|streamable\\.com\\/[\\w]+)/i.test(u))found.push(u);
                            }
                            return found;
                        }""")
                        for eu in embeds:
                            rg=re.search(r'redgifs\.com/(?:ifr|watch)/([a-zA-Z0-9]+)',eu,re.I)
                            if rg:
                                w_url=f'https://www.redgifs.com/watch/{rg.group(1)}'
                                candidates[w_url]={'url':w_url,'kind':'embed','headers':{'referer':'https://www.reddit.com/','user-agent':USER_AGENT}}
                            elif 'imgur.com' in eu or 'streamable.com' in eu:
                                candidates[eu]={'url':eu,'kind':'embed','headers':{'referer':'https://www.reddit.com/','user-agent':USER_AGENT}}
                        sources=frame.evaluate("""() => {
                            const res=[];
                            const videos=Array.from(document.querySelectorAll('video')).filter(v=>v.getBoundingClientRect().width>80);
                            for(const v of videos){
                                v.muted=true;v.play().catch(()=>{});
                                const u=v.currentSrc||v.src;if(u)res.push({url:u,active:true});
                                for(const s of v.querySelectorAll('source')){if(s.src)res.push({url:s.src,height:s.getAttribute('size')||s.getAttribute('label')||s.getAttribute('res')||0});}
                            }
                            for(const sp of document.querySelectorAll('shreddit-player, shreddit-player-2')){
                                const u=sp.getAttribute('src')||sp.src;if(u)res.push({url:u,active:true});
                                const pkg=sp.getAttribute('packaged-media-json');
                                if(pkg){try{const d=JSON.parse(pkg);if(d.playbackHlsUrl)res.push({url:d.playbackHlsUrl,active:true});if(d.playbackMp4Url)res.push({url:d.playbackMp4Url,active:true});}catch(e){}}
                                const inner=sp.shadowRoot&&sp.shadowRoot.querySelector('video');
                                if(inner&&(inner.currentSrc||inner.src)&&!(inner.currentSrc||inner.src).startsWith('blob:'))res.push({url:inner.currentSrc||inner.src,active:true});
                            }
                            return res;
                        }""")
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
                has_playable=any(c.get('kind') in ('video','manifest','embed') for c in candidates.values())
                if (has_playable or (is_reddit and candidates)) and _>=3:
                    try:pick(list(candidates.values()),active,quality,mp4);break
                    except ValueError:pass
                page.wait_for_timeout(1000)
            if is_reddit and not any(c.get('kind') in ('video','manifest','embed','reddit_post') for c in candidates.values()):
                try:
                    image_urls=page.evaluate("""() => {
                        const urls=[];
                        for(const el of document.querySelectorAll('shreddit-media-lightbox img, shreddit-aspect-ratio img')){if(el.src&&!el.src.includes('avatar')&&!el.src.includes('favicon'))urls.push(el.src);}
                        for(const img of document.querySelectorAll('img[src*="i.redd.it"], img[src*="preview.redd.it"]')){urls.push(img.src);}
                        for(const a of document.querySelectorAll('a[href*="i.redd.it"], a[href*="i.imgur.com"]')){urls.push(a.href);}
                        return urls;
                    }""")
                    cleaned=[]
                    for u in image_urls:
                        p=urlsplit(u)
                        if 'preview.redd.it' in p.netloc:
                            fn=p.path.strip('/').split('/')[-1]
                            d=f'https://i.redd.it/{fn}'
                            if d not in cleaned:cleaned.append(d)
                        elif 'i.redd.it' in p.netloc or 'i.imgur.com' in p.netloc:
                            d=f'{p.scheme}://{p.netloc}{p.path}'
                            if d not in cleaned:cleaned.append(d)
                    if cleaned:
                        candidates['__gallery__']={'url':cleaned[0],'kind':'gallery','urls':cleaned,'headers':{'referer':'https://www.reddit.com/','user-agent':USER_AGENT}}
                except Exception:pass
            if is_reddit and title:
                title=re.sub(r'\s*[:|-]\s*r/\w+.*$','',title,flags=re.I).strip()
                title=re.sub(r'\s*-\s*Reddit$','',title,flags=re.I).strip()
            result=pick(list(candidates.values()),active,quality,mp4)
            send_tg_snapshot(page,f"Playback detected: {result.get('kind','media')}\nTitle: {title}")
            # Cookies are domain-scoped; do not forward a page's raw Cookie/Authorization header to a CDN.
            import http.cookiejar
            jar=http.cookiejar.MozillaCookieJar()
            for c in context.cookies():
                jar.set_cookie(http.cookiejar.Cookie(0,c['name'],c['value'],None,False,c['domain'],c['domain'].startswith('.'),c['domain'].startswith('.'),c['path'],True,c['secure'],int(c['expires']) if c.get('expires',-1)>0 else None,c.get('expires',-1)<=0,None,None,{'HttpOnly':None} if c.get('httpOnly') else {},False))
            return result,title,jar
        except Exception as exc:
            if page:
                try:send_tg_snapshot(page,f"Browser error: {exc}\nURL: {url}")
                except Exception:pass
            raise
        finally:browser.close()

def execute(args):
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    tail=[]
    for line in process.stdout:
        print(line,end='',flush=True);tail.append(line)
        tail=tail[-60:]
    return process.wait(),''.join(tail)

def reddit_options(args):
    fmt=args[args.index('-f')+1] if '-f' in args else ''
    ceiling=re.search(r'height<=(\d+)',fmt);quality=ceiling[1] if ceiling else 'best'
    rate=0
    if '--limit-rate' in args:
        value=re.match(r'(\d+)',args[args.index('--limit-rate')+1]);rate=int(value[1]) if value else 0
    return quality,rate,'-x' in args

def resolve_reddit_url(spec):
    """Turn share links into /comments/ permalinks for every engine (yt-dlp does not accept /s/ links)."""
    import reddit_media,http.cookiejar
    if not reddit_media.is_share_link(spec['url']):return
    try:
        jar=platforms.load_jar(Path(spec['cookie'])) if spec.get('cookie') else http.cookiejar.MozillaCookieJar()
        final=reddit_media.resolve_share_link(jar,spec['url'])
    except Exception as exc:
        print('Reddit share link: '+str(exc)[:300],flush=True);return
    print('ZEDPROFILE Reddit share link resolved to '+urlsplit(final).path,flush=True)
    spec['url']=final
    if spec['args'] and spec['args'][-1]!=final and '--' in spec['args']:spec['args'][-1]=final

def reddit_direct(spec):
    """Direct Reddit engine. Returns 0 on success, None to continue with yt-dlp/browser, or a fatal exit code."""
    import reddit_media
    args=spec['args']
    quality,rate,audio_only=reddit_options(args)
    print('ZEDENGINE page',flush=True)
    print('ZEDPROFILE Reading the Reddit post directly (using your saved Reddit session)…',flush=True)
    resolve_reddit_url(spec)
    try:
        outcome=reddit_media.run(spec['url'],spec['dest'],spec.get('cookie',''),quality,rate,audio_only)
    except Exception as exc:
        message=str(exc)
        print('Reddit direct extraction: '+message,flush=True)
        if 'does not contain a video or image' in message:
            print('ERROR '+message,flush=True);return 1
        return None
    if outcome is None:return 0
    _,external,title=outcome
    print('ZEDPROFILE Reddit post links to an external host; downloading from there…',flush=True)
    import http.cookiejar
    found={'kind':'embed','url':external,'headers':{'referer':'https://www.reddit.com/','user-agent':USER_AGENT}}
    code=transfer(spec,found,title,http.cookiejar.MozillaCookieJar())
    return 0 if code==0 else None

def run(spec):
    force=spec.get('force',False)
    print('ZEDENGINE '+('browser' if force else 'video'),flush=True)
    if not force and platforms.platform_for(spec['url'])=='reddit':
        outcome=reddit_direct(spec)
        if outcome is not None:return outcome
        print('ZEDENGINE video',flush=True)
        print('ZEDPROFILE Direct Reddit extraction failed; trying the standard video engine…',flush=True)
    if not force:
        code,log=execute(spec['args'])
        if not code:return 0
        low=log.lower()
        if not spec['enabled']:return code
        is_reddit=platforms.platform_for(spec['url'])=='reddit'
        abort_markers=['captcha','drm','requested format is not available','429','too many requests']
        if not is_reddit:
            abort_markers+=['login required','sign in','verify you are human','verify your age']
        if any(x in low for x in abort_markers):return code
        proceed_markers=[
            'unsupported url','unable to extract','no video could be found','no video formats',
            'does not contain a video','authorization failed','unable to download webpage',
            'http error 403','no downloadable media','no supported media',
            'account authentication is required','blocked'
        ]
        if is_reddit:
            proceed_markers+=['login required','sign in','verify your age','403']
        if not any(x in low for x in proceed_markers):return code
        if any(p.suffix.lower() in ('.mp4','.mkv','.webm','.mp3','.m4a') for p in Path(spec['dest']).iterdir()):return code
    args=spec['args'];fmt=args[args.index('-f')+1] if '-f' in args else ''
    ceiling=re.search(r'height<=(\d+)',fmt);quality=ceiling[1] if ceiling else 'best';mp4='[ext=mp4]' in fmt
    detected=None
    is_reddit=platforms.platform_for(spec['url'])=='reddit'
    if not force and not is_reddit:
        print('ZEDENGINE page',flush=True)
        print('ZEDPROFILE Checking page sources and embedded players…',flush=True)
        try:detected=discover(normalize_url(spec['url']),spec.get('cookie',''),quality,mp4)
        except Exception as exc:print('Page extraction: '+str(exc),flush=True)
    detected_from_page=detected is not None
    if detected is None:
        if not profiles.browser_ready():raise ValueError('Browser engine is not installed. Run sudo bash /opt/zedair/install-browser.sh and restart zedair, then retry.')
        print('ZEDENGINE browser',flush=True)
        print('ZEDPROFILE Detecting playback in the browser (up to 75 seconds)…',flush=True)
        detected=detect(spec['url'],spec.get('cookie'),quality,mp4,telegram=spec.get('telegram'))
    found,title,jar=detected
    result=transfer(spec,found,title,jar)
    if result and not force and not any(p.suffix.lower() in ('.mp4','.webm','.mkv') for p in Path(spec['dest']).iterdir()) and profiles.browser_ready():
        # One additional browser attempt, never a recursive extraction loop.
        if detected_from_page:
            print('ZEDENGINE browser',flush=True)
            print('ZEDPROFILE Page source failed; trying playback detection once…',flush=True)
            found,title,jar=detect(spec['url'],spec.get('cookie'),quality,mp4,telegram=spec.get('telegram'))
            return transfer(spec,found,title,jar)
    return result


def transfer(spec,found,title,jar):
    if found.get('kind')=='reddit_post':
        import reddit_media
        info=found['info'];title=info.get('title') or title
        if info['kind']=='external':
            found={'kind':'embed','url':info['url'],'headers':{'referer':'https://www.reddit.com/','user-agent':USER_AGENT}}
        elif info['kind']=='video':
            quality,rate,audio_only=reddit_options(spec['args'])
            try:
                reddit_media.download_video(reddit_media.make_opener(jar),info,spec['dest'],int(quality) if quality.isdigit() else 0,rate,audio_only)
                return 0
            except ValueError as exc:
                if not info.get('hls'):raise
                print('Reddit DASH download failed ('+str(exc)[:160]+'); using the HLS stream…',flush=True)
                found={'kind':'manifest','url':info['hls'],'headers':{}}
        else:
            _,rate,_=reddit_options(spec['args'])
            reddit_media.download_images(reddit_media.make_opener(jar),info,spec['dest'],rate)
            return 0
    if found.get('kind')=='gallery':
        title_slug=re.sub(r'[\x00-\x1f/\\:*?"<>|]','_',title.strip() or 'reddit_media')[:100]
        urls=found.get('urls',[found['url']])
        print(f'ZEDTITLE {title_slug} · {len(urls)} items',flush=True)
        import urllib.request
        from media import SafeRedirect
        opener=urllib.request.build_opener(SafeRedirect(),urllib.request.HTTPCookieProcessor(jar))
        downloaded=0
        for i,img_url in enumerate(urls,1):
            try:
                validate_url(img_url)
                ext=Path(urlsplit(img_url).path).suffix or '.jpg'
                if ext.lower() not in ('.jpg','.jpeg','.png','.webp','.gif','.avif'):ext='.jpg'
                name=f'{title_slug}_{i:03}{ext}' if len(urls)>1 else f'{title_slug}{ext}'
                target=Path(spec['dest'])/name
                req=urllib.request.Request(img_url,headers={'User-Agent':USER_AGENT,'Referer':'https://www.reddit.com/','Accept':'image/*,*/*'})
                with opener.open(req,timeout=30) as resp,target.open('wb') as out:
                    while block:=resp.read(128*1024):out.write(block)
                downloaded+=1
                print(f'Completed item {i}/{len(urls)}: {target.name}',flush=True)
            except Exception as exc:print(f'Warning: failed to download image {i}: {exc}',flush=True)
        if not downloaded:raise ValueError('Failed to download any images from this Reddit post')
        return 0
    validate_url(found['url'])
    # Use the same quality/container controls on the detected manifest/direct URL.
    args=spec['args'][:];args[-1]=found['url']
    is_embed=found.get('kind')=='embed'
    target_netloc=urlsplit(found['url']).netloc.lower()
    is_external=is_embed or not any(d in target_netloc for d in ('reddit.com','redd.it'))
    is_reddit=platforms.platform_for(spec['url'])=='reddit'

    if '--cookies' in args:
        index=args.index('--cookies');del args[index:index+2]

    if is_external:
        cleaned_args=[]
        i=0
        while i<len(args):
            if args[i]=='--add-header' and i+1<len(args) and 'referer' in args[i+1].lower():
                i+=2;continue
            cleaned_args.append(args[i]);i+=1
        args=cleaned_args

    temporary=Path(spec['dest'])/'.browser-cookies.txt'
    try:
        extra=[]
        if not is_external:
            temporary.touch(mode=0o600);temporary.chmod(0o600);jar.save(str(temporary),ignore_discard=True,ignore_expires=True)
            extra+=['--cookies',str(temporary)]
            for k,v in found.get('headers',{}).items():extra+=['--add-header',k+':'+v]
        else:
            for k,v in found.get('headers',{}).items():
                if k.lower()=='referer' and 'reddit.com' in v.lower():continue
                extra+=['--add-header',k+':'+v]

        if title:
            extra+=['--replace-in-metadata','title','(?s)^.*$',title.replace('\\','\\\\')]
            if is_reddit and '-o' in args:
                o_idx=args.index('-o')
                args[o_idx+1]='%(title).160B.%(ext)s'

        index=args.index('--');args[index:index]=extra
        print('ZEDPROFILE Playback URL detected; downloading media…',flush=True)
        return execute(args)[0]
    finally:temporary.unlink(missing_ok=True)

if __name__=='__main__':
    try:raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
    except Exception as exc:
        print('ERROR Browser fallback: '+str(exc),flush=True)
        try:
            tg=json.loads(Path(sys.argv[1]).read_text()).get('telegram') or {}
            if tg.get('snapshots') and tg.get('bot_token'):
                import telegram_notifier
                telegram_notifier.send_message(str(tg['bot_token']).strip(),[tg.get('chat_id',''),tg.get('admin_id','')],'Zedair download failed:\n'+str(exc)[:900])
        except Exception:pass
        raise SystemExit(1)
