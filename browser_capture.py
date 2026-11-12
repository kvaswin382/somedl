"""Bounded browser discovery: network + rendered player state + player JSON.

The browser is a discovery tool, not the file buffer. Only one browser may run
across worker processes. Discovery keeps collecting variants before selecting.
"""
import http.cookiejar
import json
import os
import re
import signal
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
import profiles
from media import validate_url
from download_http import UA
from engine_settings import DEFAULTS
from web_session import browser_lease,available_memory

PLAYER_JS = r'''() => {
 const roots=[document];for(let i=0;i<roots.length&&i<200;i++)for(const e of roots[i].querySelectorAll('*'))if(e.shadowRoot)roots.push(e.shadowRoot);
 const out=[];let playerIndex=0;
 for(const root of roots){
  for(const v of root.querySelectorAll('video,audio')){
   const r=v.getBoundingClientRect();if(r.width<60)continue;
   v.muted=true;v.play().catch(()=>{});
   const group=v.id||'player-'+playerIndex++;
   if(v.currentSrc||v.src)out.push({url:v.currentSrc||v.src,active:true,group,height:v.videoHeight||0,dom:true});
   for(const s of v.querySelectorAll('source'))if(s.src)out.push({url:s.src,group,height:parseInt(s.getAttribute('size')||s.getAttribute('label')||s.getAttribute('res'))||0,dom:true});
  }
  for(const e of root.querySelectorAll('shreddit-player')){
   try{const d=JSON.parse(e.getAttribute('packaged-media-json')||'{}');for(const k of ['playbackHlsUrl','playbackDashUrl','playbackMp4Url'])if(d[k])out.push({url:d[k],dom:true,group:'reddit'});}catch(e){}
  }
 }
 return out;
}'''


def kind(url,mime=''):
    path=urlsplit(url).path.lower();mime=mime.lower().split(';')[0]
    if path.endswith(('.ts','.m4s','.vtt')):return ''
    if path.endswith(('.m3u8','.mpd')) or mime in ('application/vnd.apple.mpegurl','application/x-mpegurl','application/dash+xml'):return 'manifest'
    if mime.startswith(('video/','audio/')) or path.endswith(('.mp4','.webm','.m4v','.mov','.mp3','.m4a','.ogg')):return 'video'
    return ''


def json_sources(value,base='',depth=0):
    if depth>16:return []
    found=[]
    if isinstance(value,dict):
        for key,v in value.items():
            if isinstance(v,str) and key.lower() in ('file','src','url','video_url','contenturl','playbackhlsurl','playbackdashurl','hls','dash') and kind(v):
                found.append({'url':v,'height':value.get('height') or value.get('quality') or 0,'group':base})
            elif isinstance(v,(dict,list)):found.extend(json_sources(v,base,depth+1))
    elif isinstance(value,list):
        for v in value[:500]:found.extend(json_sources(v,base,depth+1))
    return found


def select(candidates,active,quality='best'):
    items=[c for c in candidates if c.get('kind') in ('video','manifest')]
    if quality.isdigit():items=[c for c in items if c['kind']=='manifest' or not c.get('height') or c['height']<=int(quality)]
    if not items:raise ValueError('No playable media request or rendered player source was observed')
    # Prefer an adaptive master: the initially playing MP4/variant may be low quality.
    masters=[c for c in items if c.get('master')]
    if len(masters)==1:return masters[0]
    manifests=[c for c in items if c['kind']=='manifest']
    if len(manifests)==1:return manifests[0]
    dom=[c for c in items if c.get('dom')]
    def groups(c):return set(c.get('groups') or ([c['group']] if c.get('group') else []))
    # Link a playing URL to its JSON variant group. A low rendition in the DOM
    # must not hide a higher rendition advertised by the same player's response.
    linked=[];connected=set()
    if dom:
        linked=[dom[0]];connected=groups(dom[0])
        for _ in range(len(items)):
            extra=[c for c in items if c not in linked and groups(c)&connected]
            if not extra:break
            linked.extend(extra)
            for c in extra:connected.update(groups(c))
    pool=linked if dom and all(c in linked for c in dom) else (dom or items)
    if len(pool)==1:return pool[0]
    if (pool==linked or len({c.get('group') for c in pool})==1) and all(c.get('height') for c in pool):return max(pool,key=lambda c:c['height'])
    exact=[c for c in pool if c['url'] in active]
    if len(exact)==1:return exact[0]
    raise ValueError(f'Found {len(items)} unrelated or unranked media sources; refusing to select an advert or silently choose low quality')


def probe_manifests(candidates,jar,deadline):
    """Chunked HLS responses have no Content-Length. Inspect a bounded prefix
    outside Chromium so a master is not confused with its playback variant."""
    from download_http import opener,request
    client=opener(jar)
    for item in [c for c in candidates if c['kind']=='manifest' and not c.get('master')][:8]:
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        try:
            with request(client,item['url'],item.get('headers'),timeout=min(5,remaining)) as response:
                body=response.read(512001)
                if len(body)<=512000:item['master']=b'#EXT-X-STREAM-INF' in body or bool(re.search(br'<MPD[\s>]',body))
        except Exception:pass  # Preserve the original candidate and its diagnostic.


def capture(url,jar,state,quality='best',settings=None,dest=None):
    from playwright.sync_api import sync_playwright
    settings={**DEFAULTS,**(settings or {})}
    timeout=settings['browser_timeout'];limit=settings['browser_candidates']
    with browser_lease(state):
        if available_memory()<settings['browser_min_memory_mib']*1024**2:raise ValueError('Browser waiting: insufficient host/service memory headroom; reduce concurrent downloads')
        # An evaluate() can outlive normal locator timeouts; terminate this job's
        # complete process group, not the web server, if Chromium stops responding.
        def expire():
            print('ERROR Browser discovery exceeded its hard time budget',flush=True)
            if os.getpid()==os.getpgrp():os.killpg(os.getpgrp(),signal.SIGTERM)
        timer=threading.Timer(timeout+40,expire);timer.daemon=True;timer.start()
        try:
            if profiles.BROWSERS.is_dir():os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(profiles.BROWSERS)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,chromium_sandbox=True,timeout=30000,args=['--disable-dev-shm-usage','--disable-background-networking','--disable-extensions','--renderer-process-limit=2','--js-flags=--max-old-space-size=192','--autoplay-policy=no-user-gesture-required'])
                try:
                    context=browser.new_context(accept_downloads=False,service_workers='block',viewport={'width':1100,'height':720})
                    context.set_default_timeout(1200)
                    context.add_cookies([{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path or '/','secure':c.secure,**({'expires':c.expires} if c.expires else {})} for c in jar if not c.is_expired()])
                    candidates={};active=set();errors=[];last=[time.monotonic()];validated=set()
                    trackers=[v.strip().lower() for v in settings['tracker_domains'].split(',') if v.strip()]
                    def add(value,headers=None):
                        u=value.get('url','')
                        if not u.startswith(('https://','http://')):return
                        k=value.get('kind') or kind(u)
                        if not k:return
                        height=re.search(r'(\d{3,4})',str(value.get('height',0)))
                        item={'url':u,'kind':k,'headers':headers or {'referer':url},'height':int(height[1]) if height else 0,**value}
                        item['height']=int(height[1]) if height else 0
                        item['groups']=list(set(candidates.get(u,{}).get('groups',[]))|({value['group']} if value.get('group') else set()))
                        if value.get('active'):active.add(u)
                        if u not in candidates and len(candidates)<limit:last[0]=time.monotonic();candidates[u]=item
                        elif u in candidates:candidates[u].update({k:v for k,v in item.items() if v})
                    def route(r):
                        req=r.request;host=urlsplit(req.url).hostname or ''
                        try:
                            if req.resource_type=='font' or (settings['block_trackers'] and any(host==d or host.endswith('.'+d) for d in trackers)):return r.abort()
                            origin=(urlsplit(req.url).scheme,host,urlsplit(req.url).port)
                            if origin not in validated:validate_url(req.url);validated.add(origin)
                            if req.resource_type=='media' and kind(req.url)=='video':
                                add({'url':req.url}, {k:v for k,v in req.all_headers().items() if k in ('referer','origin','user-agent','authorization')})
                                return r.abort()
                            r.continue_()
                        except Exception as exc:
                            errors.append('request: '+type(exc).__name__);r.abort()
                    context.route('**/*',route)
                    context.route_web_socket('**/*',lambda ws:ws.close())
                    def response(res):
                        try:
                            mime=res.headers.get('content-type','');k=kind(res.url,mime)
                            if res.status>=400:
                                if k or res.request.resource_type=='document':errors.append(f'HTTP {res.status} {urlsplit(res.url).hostname}{urlsplit(res.url).path}')
                                return
                            headers={k:v for k,v in res.request.all_headers().items() if k in ('referer','origin','user-agent','authorization')}
                            length=int(res.headers.get('content-length') or 0)
                            if k:
                                item={'url':res.url,'kind':k}
                                if k=='manifest' and 0<length<512000:
                                    body=res.text();item['master']='#EXT-X-STREAM-INF' in body or '<MPD' in body
                                add(item,headers)
                            elif 'json' in mime and 0<length<1024*1024:
                                for item in json_sources(res.json(),res.url):add(item,headers)
                        except Exception as exc:errors.append('response: '+type(exc).__name__)
                    context.on('response',response)
                    page=context.new_page();context.on('page',lambda p:p.close() if p!=page else None)
                    page.on('dialog',lambda d:d.dismiss())
                    started=time.monotonic();deadline=started+timeout
                    try:
                        try:page.goto(url,wait_until='domcontentloaded',timeout=min(timeout*1000,35000))
                        except Exception as exc:errors.append('navigation: '+type(exc).__name__)
                        title=page.title()
                        print('ZEDPROFILE Browser inspecting rendered players, frames and network variants…',flush=True)
                        iteration=0
                        while time.monotonic()<deadline:
                            if available_memory()<120*1024**2:raise ValueError('Browser stopped: service memory headroom below 120 MiB')
                            for frame in page.frames[:settings['browser_max_frames']]:
                                if time.monotonic()>=deadline:break
                                try:
                                    for item in frame.evaluate(PLAYER_JS):
                                        item['group']=frame.url+'#'+item.get('group','player');add(item,{'referer':frame.url})
                                    if iteration in (0,3,8):
                                        controls=frame.locator('button[aria-label*="play" i],button[title*="play" i],.vjs-big-play-button,.plyr__control--overlaid,.jw-icon-display,.fp-play,.play-button')
                                        for n in range(min(3,controls.count())):
                                            if controls.nth(n).is_visible():controls.nth(n).click(timeout=800);break
                                except Exception as exc:errors.append('player: '+type(exc).__name__)
                            if (urlsplit(page.url).hostname or '') in ('threads.com','www.threads.com','threads.net','www.threads.net'):
                                post=re.search(r'/(?:post|t)/([\w-]+)',urlsplit(page.url).path)
                                if post:
                                    try:
                                        from media import threads_media
                                        scripts=page.locator('script[type="application/json"],script[type="application/ld+json"]').evaluate_all('(els)=>{let n=0;return els.map(e=>e.textContent).filter(t=>(n+=t.length)<2000000)}')
                                        payload=''.join('<script type="application/json">'+v+'</script>' for v in scripts)
                                        entries=threads_media(payload,post[1],quality)
                                        return {'kind':'collection','items':[{'url':u,'headers':{'referer':page.url}} for u,e in entries]},title,jar
                                    except ValueError:pass
                            if iteration==2:page.mouse.wheel(0,400)
                            if candidates and time.monotonic()-started>=12 and time.monotonic()-last[0]>=settings['browser_settle']:
                                if any(c['kind']=='manifest' for c in candidates.values()):break
                                try:select(list(candidates.values()),active,quality);break
                                except ValueError:pass
                            page.wait_for_timeout(500);iteration+=1
                        for c in context.cookies():
                            jar.set_cookie(http.cookiejar.Cookie(0,c['name'],c['value'],None,False,c['domain'],c['domain'].startswith('.'),c['domain'].startswith('.'),c['path'],True,c['secure'],int(c['expires']) if c.get('expires',-1)>0 else None,c.get('expires',-1)<=0,None,None,{},False))
                        probe_manifests(list(candidates.values()),jar,time.monotonic()+10)
                        result=select(list(candidates.values()),active,quality)
                        # Preserve the exact browser identity used for the request.
                        result.setdefault('headers',{}).setdefault('user-agent',page.evaluate('navigator.userAgent'))
                        return result,title,jar
                    except Exception as exc:
                        if dest and settings.get('telegram_snapshots'):
                            try:page.screenshot(path=str(Path(dest)/'.browser-error.jpg'),type='jpeg',quality=55,timeout=3000)
                            except Exception:pass
                        detail='; '.join(dict.fromkeys(errors[-12:]))
                        raise ValueError(f'{exc}. Observed {len(candidates)} candidates across {len(page.frames)} frames. {detail}') from exc
                    finally:
                        for c in context.cookies():
                            jar.set_cookie(http.cookiejar.Cookie(0,c['name'],c['value'],None,False,c['domain'],c['domain'].startswith('.'),c['domain'].startswith('.'),c['path'],True,c['secure'],int(c['expires']) if c.get('expires',-1)>0 else None,c.get('expires',-1)<=0,None,None,{},False))
                finally:browser.close()
        finally:timer.cancel()
