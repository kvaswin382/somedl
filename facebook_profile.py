"""Optional Facebook profile collector. One sandboxed browser; no challenge bypass.

Facebook has no supported whole-profile extractor here. This is explicitly best-effort:
scroll the selected profile views, retain canonical links in SQLite, then download the
accessible media. Network/browser failures never become a claim of a complete archive.
"""
import http.cookiejar
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import platforms
from media import validate_url


def say(message):
    print('ZEDPROFILE '+message, flush=True)


def media_host(url):
    p=urllib.parse.urlsplit(url)
    return p.scheme=='https' and not p.username and not p.password and p.port in (None,443) and any(
        (p.hostname or '')==d or (p.hostname or '').endswith('.'+d) for d in ('fbcdn.net','fbsbx.com'))


def canonical_link(value, username, scope='posts'):
    p=urllib.parse.urlsplit(urllib.parse.urljoin('https://www.facebook.com/',value))
    if p.scheme!='https' or p.hostname not in ('www.facebook.com','m.facebook.com','facebook.com') or p.username or p.password or p.port not in (None,443):return None
    path=p.path.rstrip('/'); q=urllib.parse.parse_qs(p.query)
    if scope=='reels':
        match=re.fullmatch(r'/reel/(\d+)',path)
        return ('video','reel-'+match[1],'https://www.facebook.com/reel/'+match[1]) if match else None
    if path in ('/photo','/photo.php') and q.get('fbid',[''])[0].isdigit():
        ident=q['fbid'][0];return 'photo','photo-'+ident,'https://www.facebook.com/photo.php?fbid='+ident
    match=re.fullmatch('/'+re.escape(username)+r'/videos/(\d+)',path,re.I)
    if match:return 'video','video-'+match[1],'https://www.facebook.com'+path
    match=re.fullmatch('/'+re.escape(username)+r'/posts/([A-Za-z0-9]+)',path,re.I)
    if match:return 'post','post-'+match[1],'https://www.facebook.com'+path
    if path in ('/story.php','/permalink.php') and q.get('id',[''])[0]==username and re.fullmatch(r'[A-Za-z0-9]+',q.get('story_fbid',[''])[0]):
        ident=q['story_fbid'][0];return 'post','post-'+ident,'https://www.facebook.com/permalink.php?'+urllib.parse.urlencode({'story_fbid':ident,'id':username})
    return None


def photo_source(documents, photo_id):
    """Only use image fields belonging to the exact requested photo ID, not thumbnails/avatars."""
    matches=[]
    stack=list(documents);visited=0
    while stack and visited<100000:
        node=stack.pop();visited+=1
        if isinstance(node,list):stack.extend(node)
        elif isinstance(node,dict):
            if str(node.get('id',''))==photo_id or str(node.get('fbid',''))==photo_id:
                for key in ('image','photo_image','viewer_image','image_full','largest_image'):
                    image=node.get(key)
                    if isinstance(image,dict) and media_host(image.get('uri','')):
                        try:area=int(image.get('width',0))*int(image.get('height',0))
                        except (ValueError,TypeError):area=0
                        matches.append((area,image['uri']))
            stack.extend(v for v in node.values() if isinstance(v,(dict,list)))
    return max(matches,default=(0,None))[1]


def browser_cookies(jar):
    return [dict(name=c.name,value=c.value,domain=c.domain,path=c.path or '/',secure=bool(c.secure),
                 httpOnly=c.has_nonstandard_attr('HttpOnly'),expires=c.expires if c.expires else -1)
            for c in jar if c.value and (c.expires is None or c.expires>time.time()) and
            (c.domain.lstrip('.')=='facebook.com' or c.domain.endswith('.facebook.com'))]


def save_browser_cookies(path, cookies):
    jar=http.cookiejar.MozillaCookieJar(str(path))
    for c in cookies:
        if c['domain'].lstrip('.')!='facebook.com' and not c['domain'].endswith('.facebook.com'):continue
        expires=c.get('expires',-1)
        jar.set_cookie(http.cookiejar.Cookie(0,c['name'],c['value'],None,False,c['domain'],c['domain'].startswith('.'),
            c['domain'].startswith('.'),c.get('path','/'),True,c.get('secure',False),int(expires) if expires>0 else None,
            expires<=0,None,None,{'HttpOnly':None} if c.get('httpOnly') else {}))
    jar.save(ignore_discard=True,ignore_expires=True);Path(path).chmod(0o600)


class MediaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not media_host(newurl):raise ValueError('Unexpected photo redirect; download refused')
        validate_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def download_photo(url,destination,rate_kib=0):
    if not media_host(url):raise ValueError('Unexpected photo media host')
    validate_url(url)
    opener=urllib.request.build_opener(MediaRedirect())
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity'})
    with opener.open(req,timeout=45) as response:
        ext={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/avif':'.avif','image/gif':'.gif'}.get(response.headers.get_content_type())
        if not ext:raise ValueError('The photo URL did not return a supported image')
        path=destination.with_suffix(ext);temp=path.with_suffix(ext+'.part');total=0;started=time.monotonic()
        with temp.open('wb') as out:
            while data:=response.read(256*1024):
                total+=len(data)
                if total>250*1024*1024:raise ValueError('Photo exceeds the 250 MiB per-image safety limit')
                out.write(data)
                if rate_kib:time.sleep(max(0,total/(rate_kib*1024)-(time.monotonic()-started)))
            out.flush();os.fsync(out.fileno())
        if not total:raise ValueError('Empty photo response')
        os.replace(temp,path)
    print('ZEDITEM photo',flush=True)


def check_page(page):
    url=urllib.parse.urlsplit(page.url)
    if url.hostname not in ('facebook.com','www.facebook.com','m.facebook.com'):
        raise ValueError('Unexpected Facebook redirect; collection stopped')
    if any(x in url.path for x in ('/login','/checkpoint','/challenge','/two_step_verification')):
        raise ValueError('challenge_required: Facebook needs sign-in or verification in your own browser')
    for selector in ('input[name="email"]','input[name="pass"]'):
        if page.locator(selector).first.is_visible():
            raise ValueError('login_required: Facebook did not accept the saved session')
    body=page.locator('body').inner_text(timeout=10000)[:12000].lower()
    if any(x in body for x in ('confirm you are human','confirm your identity','you’re temporarily blocked','you are temporarily blocked','we limit how often')):
        raise ValueError('challenge_required: Facebook asked for verification or limited access; collection stopped')


def collect(config, db):
    from playwright.sync_api import sync_playwright
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=config['browsers']
    username=config['username'];base=config['url'];mode=config['mode'];limit=config['scroll_limit']
    streams=(['posts','photos'] if mode=='posts' else ['reels'] if mode=='reels' else ['posts','photos','reels'])
    limited=False;failures=0
    def remember(link):
        if link:
            kind,key,url=link
            db.execute('INSERT OR IGNORE INTO media(key,kind,url,done) VALUES(?,?,?,0)',(key,kind,url));db.commit()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,chromium_sandbox=True,args=['--disable-dev-shm-usage','--renderer-process-limit=2'])
        try:
            context=browser.new_context(viewport={'width':1100,'height':800},service_workers='block',accept_downloads=False)
            context.add_cookies(browser_cookies(platforms.load_jar(config['cookie'])))
            def route(request_route):
                req=request_route.request;p=urllib.parse.urlsplit(req.url);host=p.hostname or ''
                allowed=p.scheme=='https' and any(host==d or host.endswith('.'+d) for d in ('facebook.com','fbcdn.net','fbsbx.com','facebook.net'))
                if not allowed or req.resource_type in ('image','media','font'):request_route.abort()
                else:request_route.continue_()
            context.route('**/*',route)
            page=context.new_page();page.set_default_timeout(15000)
            for stream in streams:
                suffix={'posts':'','photos':'photos','reels':'reels_tab'}[stream]
                say('Collecting '+stream+' from '+username+'…')
                page.goto(base+suffix,wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(2500);check_page(page)
                stale=0;previous=-1
                for step in range(limit):
                    check_page(page)
                    # Restrict collection to the main content area, not side navigation.
                    for href in page.locator('[role="main"] a[href]').evaluate_all('(nodes)=>nodes.map(n=>n.href)'):
                        remember(canonical_link(href,username,'reels' if stream=='reels' else 'posts'))
                    count=db.execute('SELECT count(*) FROM media').fetchone()[0]
                    say(f'Collecting {stream}: {count} unique links · scroll {step+1}')
                    stale=stale+1 if count==previous else 0;previous=count
                    if stale>=6:break
                    page.mouse.wheel(0,1800);page.wait_for_timeout(2500)
                else:limited=True
            # Expand photo/carousel links only from each explicitly collected post.
            for key,url in db.execute("SELECT key,url FROM media WHERE kind='post' AND done=0").fetchall():
                page.goto(url,wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(2000);check_page(page)
                main=page.locator('[role="main"]');article=main.locator('[role="article"]').first
                scope=article if article.count() else main
                for href in scope.locator('a[href]').evaluate_all('(nodes)=>nodes.map(n=>n.href)'):
                    link=canonical_link(href,username)
                    if link and link[0]!='post':remember(link)
                if scope.locator('video').count():remember(('video','video-'+key,url))
                db.execute('UPDATE media SET done=1 WHERE key=?',(key,));db.commit()
            # Fetch the exact photo object's full image field. No avatar/OG fallback.
            for key,url in db.execute("SELECT key,url FROM media WHERE kind='photo' AND done=0").fetchall():
                documents=[];budget=[0]
                def receive(response):
                    if '/api/graphql' not in response.url:return
                    try:
                        if int(response.headers.get('content-length','0'))>8*1024*1024:return
                        if budget[0]>8*1024*1024:return
                        raw=response.text();budget[0]+=len(raw)
                        if len(raw)>8*1024*1024:return
                        for line in raw.splitlines():
                            try:documents.append(json.loads(line.removeprefix('for (;;);')))
                            except ValueError:pass
                    except Exception:pass
                page.on('response',receive)
                try:
                    say('Reading '+key+'…');page.goto(url,wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(2000);check_page(page)
                    texts=page.locator('script[type="application/json"]').evaluate_all('(nodes)=>{let size=0;return nodes.map(n=>n.textContent).filter(t=>(size+=t.length)<8000000)}')
                    for value in texts:
                        try:documents.append(json.loads(value))
                        except ValueError:pass
                    source=photo_source(documents,key.removeprefix('photo-'))
                    if not source:raise ValueError('Exact full-size photo data was unavailable')
                    download_photo(source,Path(config['dest'])/key,config['media_kib'])
                    db.execute('UPDATE media SET done=1 WHERE key=?',(key,));db.commit()
                except ValueError as exc:
                    if 'required:' in str(exc):raise
                    failures+=1;print('Photo unavailable: '+key+'; '+str(exc),flush=True)
                finally:page.remove_listener('response',receive)
            save_browser_cookies(config['cookie'],context.cookies())
        finally:browser.close()
    return limited,failures


def run(config):
    dest=Path(config['dest']);db=sqlite3.connect(dest/'.facebook-profile.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS media(key TEXT PRIMARY KEY,kind TEXT,url TEXT,done INTEGER)')
    try:
        limited,failures=collect(config,db)
        count=db.execute("SELECT count(*) FROM media WHERE kind!='post'").fetchone()[0]
        if not count:raise ValueError('No accessible media found. Facebook may require verification or its page layout may have changed.')
        # Browser is closed before FFmpeg/video downloading to reduce peak memory.
        for key,url in db.execute("SELECT key,url FROM media WHERE kind='video' AND done=0").fetchall():
            say('Downloading '+key+'…')
            args=[sys.executable,'-m','yt_dlp','--ignore-config','--no-playlist','--no-overwrites','--continue',
                  '--cookies',config['cookie'],'--socket-timeout','30','--retries','2','--fragment-retries','2',
                  '--concurrent-fragments','1','--windows-filenames','--no-colors','--newline',
                  '-f','bv*+ba/b','--postprocessor-args','ffmpeg:-threads 1','-P',str(dest),
                  '-o','%(title).100B [%(id)s].%(ext)s']
            if config['media_kib']:args+=['--limit-rate',str(config['media_kib'])+'K']
            process=subprocess.Popen(args+['--',url],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace')
            tail=''
            for line in process.stdout:
                print(line,end='',flush=True)
                tail=(tail+line)[-12000:]
            code=process.wait()
            if code:
                category,_=platforms.failure_kind(tail)
                if category in ('auth','rate_limit','challenge','access'):
                    raise ValueError('Video download stopped: '+platforms.redact(tail[-4000:],Path(config['cookie']).parent))
                failures+=1
            else:
                db.execute('UPDATE media SET done=1 WHERE key=?',(key,));db.commit();print('ZEDITEM video',flush=True)
        if failures:raise ValueError(f'{failures} media items failed. Successfully downloaded files were retained. Retry this profile after checking the log.')
        if limited:raise ValueError('Collection reached the configured scroll limit. Saved available items; completeness is unverified. Increase the limit in Tools and retry.')
        say(f'{count} accessible media links processed. Facebook completeness is unverified; hidden/restricted content may be absent.')
    finally:db.close()


if __name__=='__main__':
    try:
        cfg=json.loads(Path(sys.argv[1]).read_text());run(cfg)
    except Exception as exc:
        print('Profile collector stopped: '+str(exc),file=sys.stderr,flush=True);sys.exit(1)
