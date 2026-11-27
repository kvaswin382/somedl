"""Instagram browser fallback. Accept structured media only in a verified scope.

No arbitrary CDN sniffing: adverts, profile icons and video posters are not posts.
The caller owns the download lease for both discovery and transfer.
"""
import http.cookiejar
import json
import os
import re
import time
import contextlib
import signal
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from engine_settings import DEFAULTS
from media import validate_url
from web_session import available_memory, browser_lease


def walk(value):
    stack=[(value,0)]; count=0
    while stack and count<40000:
        item,depth=stack.pop();count+=1
        if depth>32:continue
        if isinstance(item,dict):
            yield item
            stack.extend((v,depth+1) for v in item.values() if isinstance(v,(dict,list)))
        elif isinstance(item,list):stack.extend((v,depth+1) for v in item[:1000])


def owner(node):
    return str((node.get('owner') or node.get('user') or {}).get('username') or '').lower()


def target(spec):
    parts=urlsplit(spec['url']).path.strip('/').split('/')
    mode=spec.get('mode') or ''
    if parts[0]=='s':
        from instagram_story import target as story_target
        _,url=story_target(spec['url'],'highlights');parts=urlsplit(url).path.strip('/').split('/')
    if parts[0] in ('p','reel','reels','tv') and len(parts)>1:
        return {'kind':'post','code':parts[1],'url':'https://www.instagram.com/p/'+parts[1]+'/'}
    if parts[:2]==['stories','highlights'] and len(parts)>2:
        return {'kind':'highlight','id':parts[2],'url':'https://www.instagram.com/stories/highlights/'+parts[2]+'/'}
    if parts[0]=='stories' and len(parts)>1:
        return {'kind':'stories','user':parts[1].lower(),'id':parts[2] if len(parts)>2 else '', 'url':spec['url']}
    user=spec.get('username') or parts[0]
    if not re.fullmatch(r'[A-Za-z0-9_.]{1,30}',user):raise ValueError('Instagram fallback needs a specific profile or media URL')
    return {'kind':mode or 'posts','user':user.lower(),'url':'https://www.instagram.com/'+user+('/reels/' if mode=='reels' else '/')}


def number(value):
    try:return max(0,int(value or 0))
    except (ValueError,TypeError):return 0


def best(variants,quality='best'):
    variants=[v for v in variants if isinstance(v,dict) and isinstance(v.get('url') or v.get('src'),str)]
    if str(quality).isdigit():
        lower=[v for v in variants if number(v.get('height') or v.get('config_height'))<=int(quality)]
        if lower:variants=lower
    if not variants:return None
    return max(variants,key=lambda v:(number(v.get('height') or v.get('config_height'))*number(v.get('width') or v.get('config_width')),number(v.get('bandwidth'))))


def dash_sources(value,quality='best'):
    """Instagram embeds progressive DASH representations in its media objects.
    Never hand an untrusted manifest to ffmpeg's network protocols.
    """
    import xml.etree.ElementTree as ET
    if not isinstance(value,str) or len(value)>1024*1024 or '<!DOCTYPE' in value.upper() or '<!ENTITY' in value.upper():return None
    try:root=ET.fromstring(value)
    except ET.ParseError:return None
    if any(e.tag.split('}')[-1] in ('SegmentTemplate','SegmentList') for e in root.iter()):return None
    video=[];audio=[]
    for group in root.iter():
        if group.tag.split('}')[-1]!='AdaptationSet':continue
        for rep in group:
            if rep.tag.split('}')[-1]!='Representation':continue
            base=next((e.text for e in rep if e.tag.split('}')[-1]=='BaseURL'),None)
            if not base or urlsplit(base).scheme!='https':continue
            item={**group.attrib,**rep.attrib,'url':base}
            mime=item.get('mimeType','');kind=item.get('contentType','')
            if mime.startswith('video/') or kind=='video':video.append(item)
            elif mime.startswith('audio/') or kind=='audio':audio.append(item)
    chosen=best(video,quality)
    sound=max(audio,key=lambda a:number(a.get('bandwidth'))) if audio else None
    return (chosen,sound) if chosen and sound else None


def media(node,key,quality):
    video=node.get('is_video') is True or str(node.get('media_type'))=='2' or node.get('__typename') in ('GraphVideo','XDTGraphVideo') or bool(node.get('video_versions') or node.get('video_url'))
    if video:
        choices=list(node.get('video_versions') or node.get('video_resources') or [])
        if node.get('video_url'):choices.append({'url':node['video_url'],**(node.get('dimensions') or {})})
    else:
        choices=list((node.get('image_versions2') or {}).get('candidates') or node.get('display_resources') or [])
        if node.get('display_url'):choices.append({'url':node['display_url'],**(node.get('dimensions') or {})})
    selected=best(choices,quality if video else 'best')
    dash=dash_sources(node.get('video_dash_manifest'),quality) if video else None
    if dash and (not selected or number(dash[0].get('height'))>=number(selected.get('height') or selected.get('config_height'))):
        return {'key':key,'url':dash[0]['url'],'audio_url':dash[1]['url'],'video':True,'owner':owner(node),'rank':(number(dash[0].get('height'))*number(dash[0].get('width')),number(dash[0].get('bandwidth')))}
    if not selected:raise ValueError('Instagram omitted full '+('video' if video else 'image')+' for '+key+'; refusing to substitute a thumbnail')
    url=selected.get('url') or selected.get('src')
    if urlsplit(url).scheme!='https':raise ValueError('Instagram supplied an unsafe media URL')
    return {'key':key,'url':url,'video':video,'owner':owner(node),'rank':(number(selected.get('height') or selected.get('config_height'))*number(selected.get('width') or selected.get('config_width')),number(selected.get('bandwidth')))}


def post(node,quality='best'):
    code=node.get('shortcode') or node.get('code')
    if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',code):return []
    side=node.get('edge_sidecar_to_children') or {}
    children=node.get('carousel_media') or [e.get('node',{}) for e in side.get('edges',[])]
    is_carousel=node.get('media_type')==8 or node.get('__typename')=='GraphSidecar' or bool(children)
    if is_carousel:
        expected=number(node.get('carousel_media_count') or side.get('count'))
        if not children or (expected and len(children)!=expected):raise ValueError('Instagram returned an incomplete carousel for '+code)
    else:children=[node]
    result=[]
    for i,child in enumerate(children,1):
        item=media(child,code+'_'+str(i).zfill(2),quality);item['owner']=owner(node);result.append(item)
    return result


class Selection:
    def __init__(self,scope,quality='best',images_only=False):
        self.scope=scope;self.quality=quality;self.images_only=images_only
        self.items={};self.errors=[];self.links=set();self.complete=False;self.matched=False;self.user_id='';self.required={};self.failures={};self.complete_counts={}

    def add_post(self,node):
        code=node.get('shortcode') or node.get('code')
        if not isinstance(code,str):return
        expected=number(node.get('carousel_media_count') or (node.get('edge_sidecar_to_children') or {}).get('count'))
        self.required[code]=max(self.required.get(code,0),expected)
        try:
            items=post(node,self.quality)
            if len(items)<self.required[code]:
                if self.complete_counts.get(code,0)>=self.required[code]:return
                raise ValueError('Instagram returned an incomplete carousel for '+code)
            self.add(items);self.failures.pop(code,None);self.complete_counts[code]=len(items)
        except (ValueError,TypeError,AttributeError) as exc:self.failures[code]=str(exc)

    def connection(self,feed):
        s=self.scope
        entries=feed.get('edges') if 'edges' in feed else feed.get('items',[])
        for entry in entries or []:
            child=entry.get('node',entry);child=child.get('media',child)
            if s['kind']=='highlights':
                identifier=str(child.get('id') or child.get('pk') or '').removeprefix('highlight:')
                if identifier.isdigit() and (not owner(child) or owner(child)==s['user']):self.links.add(identifier)
            elif not owner(child) or owner(child)==s['user']:
                if not owner(child):child={**child,'owner':{'username':s['user']}}
                self.add_post(child)
        if feed.get('page_info',{}).get('has_next_page') is False or feed.get('more_available') is False:self.complete=True

    def ingest(self,data,requested=False):
        s=self.scope
        # Only a request explicitly bound to this user may establish pagination
        # completion. An unrelated recommendations connection cannot do so.
        if requested:
            for node in walk(data):
                for key,value in node.items():
                    if isinstance(value,dict) and (key in ('feed','highlights','edge_owner_to_timeline_media','edge_clips_media','edge_highlight_reels') or 'feed__user_timeline' in key or 'clips__user' in key):
                        if 'edges' in value or 'items' in value:self.connection(value)
                if 'items' in node and 'more_available' in node:self.connection(node)
                if s['kind']=='highlights' and isinstance(node.get('tray'),list):
                    self.connection({'items':node['tray'],'more_available':False})

        for node in walk(data):
            try:
                code=node.get('shortcode') or node.get('code')
                if isinstance(code,str) and ((s['kind']=='post' and code==s['code']) or (s.get('user') and owner(node)==s['user'])):
                    expected=number(node.get('carousel_media_count') or (node.get('edge_sidecar_to_children') or {}).get('count'))
                    self.required[code]=max(self.required.get(code,0),expected)
                if s['kind']=='post' and code==s['code']:
                    self.add_post(node);self.matched=True;self.complete=bool(self.items)
                elif s['kind'] in ('posts','reels','everything') and owner(node)==s['user'] and code:
                    if s['kind']=='reels' and node.get('product_type')!='clips':continue
                    self.add_post(node);self.matched=True
                elif s['kind']=='avatar' and str(node.get('username','')).lower()==s['user']:
                    hd=node.get('hd_profile_pic_url_info') or {}
                    url=hd.get('url') or node.get('profile_pic_url_hd')
                    if url:
                        self.add([{'key':s['user']+'_avatar','url':url,'video':False,'owner':s['user']}]);self.matched=True;self.complete=True
                elif s['kind']=='stories' and s.get('id') and str(node.get('pk') or node.get('id') or '').split('_')[0]==s['id'] and owner(node)==s['user']:
                    self.add([media(node,'story_'+s['id'],self.quality)]);self.matched=True;self.complete=bool(self.items)
                elif s['kind'] in ('highlight','stories') and isinstance(node.get('items'),list):
                    identifier=str(node.get('id') or node.get('pk') or '')
                    matches=(identifier in (s.get('id'),'highlight:'+s.get('id',''))) if s['kind']=='highlight' else owner(node)==s['user']
                    if not matches:continue
                    items=node['items'];expected=number(node.get('media_count'))
                    if expected and len(items)!=expected:raise ValueError('Instagram returned an incomplete story/highlight collection')
                    selected=[]
                    for child in items:
                        identity=str(child.get('pk') or child.get('id') or '').split('_')[0]
                        if not identity.isdigit():continue
                        if s['kind']=='stories' and s.get('id') and identity!=s['id']:continue
                        item=media(child,'story_'+identity,self.quality);item['owner']=owner(node);selected.append(item)
                    self.add(selected);self.matched=bool(selected);self.complete=self.matched
                if s.get('user') and str(node.get('username','')).lower()==s['user']:
                    self.user_id=str(node.get('id') or node.get('pk') or self.user_id)
                    feed_key={'posts':'edge_owner_to_timeline_media','reels':'edge_clips_media','highlights':'edge_highlight_reels'}.get(s['kind'])
                    if feed_key and isinstance(node.get(feed_key),dict):self.connection(node[feed_key])
            except (ValueError,TypeError,AttributeError) as exc:self.errors.append(str(exc));self.errors=self.errors[-10:]

    def add(self,items):
        for item in items:
            if not self.images_only or not item['video']:
                old=self.items.get(item['key'])
                if not old or item.get('rank',(0,0))>=old.get('rank',(0,0)):self.items[item['key']]=item

    def result(self):
        if self.failures:raise ValueError(next(iter(self.failures.values())))
        if not self.images_only:
            for code,expected in self.required.items():
                if sum(k.startswith(code+'_') for k in self.items)<expected:raise ValueError('Instagram returned an incomplete carousel for '+code)
        if self.scope['kind']=='highlights' and self.links:
            return [{'collection_url':'https://www.instagram.com/stories/highlights/'+i+'/'} for i in sorted(self.links)]
        if not self.items:
            raise ValueError(self.errors[-1] if self.errors else 'Instagram did not expose full media belonging to the requested '+self.scope['kind']+'. Login, expiry, or a changed page format may be responsible; unrelated media was not substituted.')
        return list(self.items.values())



def rendered_post(page,scope,deadline):
    """Last resort for an exact post article, never the surrounding feed.
    Enumerate carousel controls to the end; a blob-only video is not an image.
    """
    if scope['kind']!='post':return []
    code=scope['code']
    current=re.search(r'/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)',urlsplit(page.url).path)
    if not current or current[1]!=code:return []
    articles=page.locator('article')
    if articles.count()!=1:return []
    article=articles.first
    links=article.locator('a[href]').evaluate_all('(els)=>els.map(e=>e.href)')
    post_codes={m[1] for u in links if (m:=re.search(r'/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)',urlsplit(u).path))}
    if post_codes and post_codes!={code}:return []
    found={};visited=set()
    for _ in range(100):
        if time.monotonic()>deadline:raise ValueError('Instagram carousel did not finish within the browser time limit')
        values=article.evaluate(r"""el => {
          const out=[];
          for (const e of el.querySelectorAll('video,img')) {
            if(e.closest('header,footer,nav,aside') || /profile picture/i.test(e.alt||'')) continue;
            const r=e.getBoundingClientRect(); if(r.width<180 || r.height<120)continue;
            if(e.tagName==='VIDEO') {out.push({video:true,url:e.currentSrc||e.src||'',height:e.videoHeight,width:e.videoWidth});continue;}
            if(e.closest('div')?.querySelector('video'))continue;
            const variants=(e.srcset||'').split(',').map(s=>{const [url,w]=s.trim().split(/\s+/);return {url,w:parseInt(w)||0}}).filter(v=>v.url).sort((a,b)=>b.w-a.w);
            out.push({video:false,url:variants[0]?.url||e.currentSrc||e.src,height:e.naturalHeight,width:e.naturalWidth});
          } return out;
        }""")
        if any(v['video'] for v in values):values=[v for v in values if v['video']]
        for value in values:
            if not value['url'].startswith('https://'):
                if value['video']:raise ValueError('Instagram exposes only a blob player; full video metadata is unavailable')
                continue
            identity=urlsplit(value['url']).path
            if identity not in found:found[identity]={**value,'key':code+'_'+str(len(found)+1).zfill(2),'owner':''}
        next_button=article.locator('button[aria-label="Next"],button[title="Next"]')
        if not next_button.count() or not next_button.first.is_visible():return list(found.values())
        signature=tuple(v['url'] for v in values)
        if signature in visited:raise ValueError('Instagram carousel stopped advancing; refusing a partial carousel')
        visited.add(signature)
        next_button.first.click(timeout=1200);page.wait_for_timeout(900)
    raise ValueError('Instagram carousel exceeded the supported item limit')

@contextlib.contextmanager
def browser_deadline(seconds):
    def expired():
        print('ERROR Instagram browser exceeded its hard discovery time budget',flush=True)
        if os.getpid()==os.getpgrp():os.killpg(os.getpgrp(),signal.SIGTERM)
    timer=threading.Timer(seconds,expired);timer.daemon=True;timer.start()
    try:yield
    finally:timer.cancel()


def _discover(spec,jar):
    from playwright.sync_api import sync_playwright
    import profiles
    settings={**DEFAULTS,**spec.get('settings',{})};scope=target(spec)
    selection=Selection(scope,spec.get('quality','best'),spec.get('images_only',False))
    with browser_lease(spec['state']):
        deadline=time.monotonic()+settings['browser_memory_wait']
        while available_memory()<settings['browser_min_memory_mib']*1024**2:
            if time.monotonic()>=deadline:raise ValueError('Instagram browser needs more memory headroom; pause other downloads or torrents and retry')
            print('ZEDPROFILE Waiting for Instagram browser RAM headroom',flush=True);time.sleep(5)
        if profiles.BROWSERS.is_dir():os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(profiles.BROWSERS)
        with browser_deadline(settings['browser_timeout']+60),sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,chromium_sandbox=True,timeout=30000,args=['--disable-dev-shm-usage','--disable-background-networking','--disable-extensions','--renderer-process-limit=2','--js-flags=--max-old-space-size=192'])
            try:
                context=browser.new_context(accept_downloads=False,service_workers='block',locale='en-US')
                context.set_default_timeout(1500)
                context.add_cookies([{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path or '/', 'secure':c.secure,'httpOnly':c.has_nonstandard_attr('HTTPOnly') or c.has_nonstandard_attr('HttpOnly'),**({'expires':c.expires} if c.expires else {})} for c in jar if not c.is_expired()])
                validated=set();trackers=[d.strip().lower() for d in settings['tracker_domains'].split(',') if d.strip()]
                def route(r):
                    try:
                        p=urlsplit(r.request.url);origin=(p.scheme,p.hostname,p.port)
                        if origin not in validated:validate_url(r.request.url);validated.add(origin)
                        if r.request.resource_type in ('font','media') or (settings['block_trackers'] and any(p.hostname==d or (p.hostname or '').endswith('.'+d) for d in trackers)):return r.abort()
                        r.continue_()
                    except Exception:r.abort()
                context.route('**/*',route);context.route_web_socket('**/*',lambda ws:ws.close())
                budget=[0];pending=[]
                def response(res):
                    try:
                        if res.status!=200 or not (urlsplit(res.url).hostname or '').endswith('.instagram.com'):return
                        if 'json' not in res.headers.get('content-type',''):return
                        length=number(res.headers.get('content-length'))
                        if length>4*1024**2 or budget[0]>=16*1024**2:return
                        raw=res.body();budget[0]+=len(raw)
                        if len(raw)>4*1024**2:return
                        for line in raw.decode('utf-8').splitlines():
                            if line.startswith('for (;;);'):line=line[9:]
                            try:
                                data=json.loads(line)
                                selection.ingest(data,requested=bound_request(res.request,selection))
                                if scope['kind'] in ('posts','reels','highlights') and not selection.user_id and budget[0]<=8*1024**2 and len(pending)<8:
                                    pending.append((data,SimpleNamespace(url=res.request.url,post_data=res.request.post_data)))
                            except ValueError:pass
                    except Exception:pass
                context.on('response',response)
                page=context.new_page();context.on('page',lambda p:p.close() if p!=page else None)
                page.on('dialog',lambda d:d.dismiss())
                deadline=time.monotonic()+settings['browser_timeout']
                try:
                    page.goto(scope['url'],wait_until='domcontentloaded',timeout=min(35000,settings['browser_timeout']*1000))
                    last=-1;settled=time.monotonic();started=settled;dom_tried=False
                    while time.monotonic()<deadline:
                        if available_memory()<120*1024**2:raise ValueError('Instagram browser stopped because memory headroom fell below 120 MiB')
                        if re.search(r'/(accounts/login|challenge|checkpoint|accounts/suspended)',urlsplit(page.url).path):
                            raise ValueError('Instagram requires login or account verification. Complete it in your own browser, then upload fresh cookies.')
                        scripts=page.locator('script[type="application/json"],script[type="application/ld+json"]').evaluate_all('(els)=>{let n=0;return els.map(e=>e.textContent).filter(t=>t.length<4000000&&(n+=t.length)<8000000)}')
                        for script in scripts:
                            try:selection.ingest(json.loads(script))
                            except ValueError:pass
                        if selection.user_id and pending:
                            for data,request in pending:selection.ingest(data,requested=bound_request(request,selection))
                            pending.clear()
                        if len(selection.items)!=last:last=len(selection.items);settled=time.monotonic()
                        if selection.complete and time.monotonic()-settled>=settings['browser_settle']:break
                        if scope['kind']=='post' and not selection.complete and not dom_tried and time.monotonic()-started>=min(12,settings['browser_timeout']/2):
                            dom_tried=True
                            items=rendered_post(page,scope,deadline)
                            if items:
                                selection.add(items);selection.failures.pop(scope['code'],None);selection.complete=True;break
                        if scope['kind'] in ('posts','reels','everything','highlights'):page.mouse.wheel(0,850)
                        else:
                            controls=page.locator('button[aria-label="Play"]')
                            if controls.count() and controls.first.is_visible():
                                try:controls.first.click(timeout=700)
                                except Exception:pass
                        page.wait_for_timeout(700)
                    items=selection.result()
                    limit=int(settings.get('playlist_limit',100))
                    if len(items)>limit:raise ValueError('Instagram collection exceeds the configured playlist item limit; increase it before retrying')
                    if not selection.complete:raise ValueError('Instagram collection is incomplete: the browser could not verify its end. No unrelated items or incomplete collection were accepted. '+('; '.join(selection.errors[-2:])))
                    agent=page.evaluate('navigator.userAgent')
                    for item in items:item['headers']={'referer':scope['url'],'user-agent':agent}
                    return items
                except Exception:
                    if settings.get('telegram_snapshots'):
                        try:page.screenshot(path=str(Path(spec['dest'])/'.browser-error.jpg'),type='jpeg',quality=55,timeout=3000)
                        except Exception:pass
                    raise
                finally:
                    for c in context.cookies():
                        jar.set_cookie(http.cookiejar.Cookie(0,c['name'],c['value'],None,False,c['domain'],c['domain'].startswith('.'),c['domain'].startswith('.'),c['path'],True,c['secure'],int(c['expires']) if c.get('expires',-1)>0 else None,c.get('expires',-1)<=0,None,None,{'HTTPOnly':''} if c.get('httpOnly') else {},False))
            finally:browser.close()


def bound_request(request,selection):
    """Bind user-feed responses using the browser's actual request variables."""
    from urllib.parse import parse_qs
    s=selection.scope
    if s['kind'] not in ('posts','reels','highlights'):return False
    user_id=selection.user_id
    path=urlsplit(request.url).path
    if user_id and (path.startswith('/api/v1/feed/user/'+user_id+'/') or path=='/api/v1/highlights/'+user_id+'/highlights_tray/'):return True
    try:
        body=request.post_data or ''
        try:data=json.loads(body)
        except ValueError:data={k:v[0] for k,v in parse_qs(body).items()}
        variables=data.get('variables',{})
        if isinstance(variables,str):variables=json.loads(variables)
        if not isinstance(variables,dict):return False
        # These variables identify a profile feed, not a post/recommendation query.
        return bool((variables.get('username')==s['user'] or (user_id and str(variables.get('id') or variables.get('user_id'))==user_id)) and not any(k in variables for k in ('shortcode','media_id')))
    except (ValueError,AttributeError,TypeError):return False


def discover(spec,jar):
    scope=target(spec)
    if scope['kind']=='everything':
        items={}
        for mode in ('posts','reels'):
            for item in _discover({**spec,'mode':mode},jar):items[item['key']]=item
        return list(items.values())
    if scope['kind']=='highlights':
        catalog=_discover(spec,jar);items={}
        for entry in catalog:
            print('ZEDPROFILE Inspecting requested profile highlight',flush=True)
            for item in _discover({**spec,'url':entry['collection_url']},jar):
                if item.get('owner')!=scope['user']:raise ValueError('Instagram highlight owner does not match requested profile')
                items[item['key']]=item
        return list(items.values())
    return _discover(spec,jar)
