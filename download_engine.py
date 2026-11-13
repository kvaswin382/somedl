"""Automatic source resolution and transfer orchestration.

Keep the submitted page URL as the durable source; signed CDN URLs are disposable.
Site extractors remain valuable, but they no longer own all file downloads.
"""
import contextlib
import html
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import download_http as http
import platforms
from media import normalize_url,validate_url,threads_media
from engine_settings import DEFAULTS
from web_session import Session


def say(message):print('ZEDPROFILE '+message,flush=True)


def diagnostic(exc):
    from notifications import sanitized
    if isinstance(exc,urllib.error.HTTPError):
        return f'HTTP {exc.code} from '+(urllib.parse.urlsplit(exc.url).hostname or '')+': '+str(exc.reason)
    return sanitized(type(exc).__name__+': '+str(exc))


def social_host(url,platform):
    host=(urllib.parse.urlsplit(url).hostname or '').lower()
    domains=('threads.net','threads.com') if platform=='threads' else ('reddit.com','redd.it')
    return any(host==d or host.endswith('.'+d) for d in domains)


def resolve_share(url,jar,timeout=20):
    platform=platforms.platform_for(url);path=urllib.parse.urlsplit(url).path
    if platform not in ('reddit','threads'):return url
    shared=(platform=='reddit' and ('/s/' in path or urllib.parse.urlsplit(url).hostname=='redd.it')) or (platform=='threads' and path.startswith('/share/'))
    if not shared:return url
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*a,**k):return None
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPCookieProcessor(jar))
    current=url
    for _ in range(8):
        validate_url(current)
        if not social_host(current,platform):raise ValueError('Share link redirected outside its platform')
        path=urllib.parse.urlsplit(current).path
        if re.search(r'/comments/[a-z0-9]+',path) if platform=='reddit' else re.search(r'/(?:post|t)/[\w-]+',path):
            p=urllib.parse.urlsplit(current)
            return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,'',''))
        try:
            with http.request(client,current,timeout=timeout) as response:
                raw=response.read(2*1024*1024).decode('utf-8','replace')
            matches=re.findall(r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)',raw,re.I)
            matches+=re.findall(r'<meta\b[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)',raw,re.I)
            if not matches:raise ValueError('Share page supplied neither a redirect nor a canonical post URL')
            current=urllib.parse.urljoin(current,html.unescape(matches[0]))
        except urllib.error.HTTPError as exc:
            if exc.code not in (301,302,303,307,308) or not exc.headers.get('Location'):raise
            current=urllib.parse.urljoin(current,exc.headers['Location'])
    raise ValueError('Share link exceeded eight redirects')


def execute(args):
    proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace')
    tail=[]
    for line in proc.stdout:
        print(line,end='',flush=True);tail.append(line);tail=tail[-50:]
    return proc.wait(),''.join(tail)


def stop_reason(log):
    low=log.lower()
    return any(s in low for s in ('drm protected','drm-protected','captcha','too many requests','http error 429','login_required','checkpoint_required'))


def gallery_args(spec,url,cookie):
    """Only invoke an actual site extractor, never the recursive generic fallback."""
    from gallery_dl import extractor
    ext=extractor.find(url)
    if ext is None or ext.category in ('generic','recursive'):return None
    if platforms.platform_for(url)=='reddit' and not re.search(r'/comments/|^https?://(?:i|preview)\.redd\.it/',url):return None
    cfg={'extractor':{'base-directory':spec['dest'],'directory':[], 'path-restrict':'windows',
                     'retries':2,'timeout':spec['settings']['http_timeout'],'cookies-update':True,
                     'archive':str(Path(spec['dest'])/'.gallery-archive.sqlite3'), 'archive-event':['file'],
                     'postprocessors':[{'name':'metadata','mode':'json'}]},
         'downloader':{'http':{'rate':str(spec['settings'].get('media_kib',0))+'k' if spec['settings'].get('media_kib') else None},
                       'ytdl':{'options':{'format':'bv*+ba/b','concurrent_fragment_downloads':1}}}}
    path=Path(spec['dest'])/'.gallery-config.json';http.atomic_json(path,cfg)
    return [sys.executable,'-m','gallery_dl','--config-ignore','--config',str(path),'--cookies',str(cookie),'--',url]


def media_args(spec,url,cookie,headers=None,stable=False):
    args=spec['args'][:];args[-1]=url
    if '--cookies' in args:
        i=args.index('--cookies');del args[i:i+2]
    extra=['--cookies',str(cookie)]
    for k,v in (headers or {}).items():
        if k.lower() in ('referer','origin','user-agent'):extra+=['--add-header',k+':'+v]
    if stable:
        # Adaptive media handed off by the browser has no entity validator for a
        # previous representation. Restart it rather than splice unknown bytes.
        extra+=['--no-continue']
        if '-o' in args:args[args.index('-o')+1]='media.%(ext)s'
    index=args.index('--');args[index:index]=extra
    return args


def finish_media(path,spec):
    """Container/audio choices happen AFTER the validated HTTP transfer.
    Copy codecs: never lower source quality with an implicit encode."""
    audio=spec.get('quality')=='audio'
    if not audio and (spec.get('container','auto')!='mp4' or path.suffix=='.mp4'):return
    ext='.mp4'
    if audio:
        probe=subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries','stream=codec_name',
                              '-of','default=nw=1:nk=1',str(path)],capture_output=True,text=True,timeout=30)
        codec=probe.stdout.strip()
        if probe.returncode or not codec:raise ValueError('The selected file contains no readable audio stream')
        ext={'aac':'.m4a','alac':'.m4a','mp3':'.mp3','opus':'.opus','vorbis':'.ogg','flac':'.flac','ac3':'.ac3','eac3':'.eac3'}.get(codec,'.mka')
    target=path.with_suffix(ext)
    if target==path:return
    temp=path.parent/('.remux-'+path.stem+ext)
    say('Copying original codecs into '+ext+'; no quality reduction.')
    process=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-i',str(path),
                            '-map','0:a:0' if audio else '0','-c','copy','-threads','1',str(temp)],capture_output=True,text=True)
    if process.returncode:
        temp.unlink(missing_ok=True)
        raise ValueError('Source codecs could not be copied into the selected container. Try Keep source container. '+process.stderr[-1200:])
    os.replace(temp,target);path.unlink();http.output_register(path.parent,target.name)


def finish_gifs(dest):
    """Convert completed GIF animations once, using one encoding thread.
    Write atomically; keep the source GIF if conversion fails or is cancelled.
    """
    for path in Path(dest).iterdir():
        if not path.is_file() or path.is_symlink() or path.name.startswith('.') or path.suffix.lower()!='.gif':continue
        target=path.with_suffix('.mp4')
        if target.exists():target=path.with_name(path.stem+'-animation.mp4')
        if target.exists():raise ValueError('Animation output already exists: '+target.name)
        temp=path.parent/('.gif-'+path.stem+'.mp4')
        say('Converting GIF to MP4 with one CPU thread.')
        result=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y',
            '-threads','1','-ignore_loop','1','-i',str(path),'-an','-vf','pad=ceil(iw/2)*2:ceil(ih/2)*2',
            '-c:v','libx264','-threads','1','-preset','veryfast','-crf','18','-pix_fmt','yuv420p',
            '-movflags','+faststart',str(temp)],capture_output=True,text=True)
        if result.returncode:
            temp.unlink(missing_ok=True)
            raise ValueError('GIF conversion failed; the original is preserved. '+result.stderr[-1000:])
        os.replace(temp,target);path.unlink();http.output_register(path.parent,target.name)


def run(spec):
    spec['settings']={**DEFAULTS,**spec.get('settings',{})}
    settings=spec['settings'];dest=Path(spec['dest']);state=Path(spec.get('state',dest))
    session=Session(state,spec['url'],spec.get('cookie',''),settings['auto_cookies'])
    client=http.opener(session.jar);jarfile=dest/'.engine-cookies.txt'
    problems=[]
    browser_job=contextlib.ExitStack()
    browser_slot=False
    def completed():
        finish_gifs(dest)
        return 0
    def save_jar():
        jarfile.touch(mode=0o600);jarfile.chmod(0o600);session.jar.save(str(jarfile),ignore_discard=True,ignore_expires=False)
    def download(found,title):
        if found.get('kind')=='collection':
            for index,item in enumerate(found['items'],1):
                info=http.inspect(client,item['url'],item.get('headers'),settings['http_timeout'])
                path=http.transfer(client,info,dest,f'{title} {index:03}',settings.get('media_kib',0),settings['http_timeout'],identity=f'item-{index}')
                if info['kind']=='video':finish_media(path,spec)
            return 0
        info=found if 'prefix' in found else http.inspect(client,found['url'],found.get('headers'),settings['http_timeout'])
        if info['kind'] in ('image','file','video','audio'):
            path=http.transfer(client,info,dest,title,settings.get('media_kib',0),settings['http_timeout'])
            if info['kind']=='video' or (info['kind']=='audio' and spec.get('quality')=='audio'):finish_media(path,spec)
            return 0
        save_jar()
        args=media_args(spec,found['url'],jarfile,found.get('headers'),stable=True)
        if spec.get('quality')!='audio':
            # HLS/DASH segments become a normal MP4, with original codecs.
            if '--remux-video' in args:args[args.index('--remux-video')+1]='mp4'
            else:args[args.index('--'):args.index('--')]=['--remux-video','mp4']
        return execute(args)[0]
    try:
        for attempt in range(settings['refresh_attempts']+1):
            save_jar()
            if attempt:say('Refreshing the original page and expiring playback URL; keeping resumable partial files.')
            source=normalize_url(spec['url'])
            try:
                resolved=resolve_share(source,session.jar,settings['http_timeout'])
                if resolved!=source:say('Resolved mobile share link to its canonical post page.')
                source=resolved
            except Exception as exc:
                problems.append('share resolver: '+diagnostic(exc));say(problems[-1])
            title=spec.get('filename') or Path(urllib.parse.urlsplit(source).path).name or 'Download'
            info=None
            try:
                info=http.inspect(client,source,timeout=settings['http_timeout'])
                if info['kind']!='page':
                    print('ZEDENGINE direct',flush=True)
                    if download(info,title)==0:return completed()
            except Exception as exc:
                problems.append('HTTP inspection/transfer: '+diagnostic(exc));say(problems[-1])
            force=bool(spec.get('force')) or attempt>0
            if not force:
                if platforms.platform_for(source)=='threads' and info and info.get('html'):
                    try:
                        code=re.search(r'/(?:post|t)/([\w-]+)',urllib.parse.urlsplit(source).path)
                        if code:
                            items=threads_media(info['html'],code[1],spec.get('quality','best'))
                            found={'kind':'collection','items':[{'url':u,'headers':{'referer':source}} for u,e in items]}
                            if download(found,spec.get('filename') or 'Threads '+code[1])==0:return completed()
                    except Exception as exc:problems.append('Threads structured post: '+diagnostic(exc))
                # Images on supported hosts must reach gallery-dl before video remuxing.
                prefer_gallery=platforms.platform_for(source)=='reddit' or (urllib.parse.urlsplit(source).hostname or '').endswith(('ibb.co','imgbb.com'))
                stages=('gallery','video') if prefer_gallery else ('video','gallery')
                for stage in stages:
                    save_jar();print('ZEDENGINE '+stage,flush=True)
                    try:
                        args=gallery_args(spec,source,jarfile) if stage=='gallery' else media_args(spec,source,jarfile)
                        if not args:continue
                        status,log=execute(args)
                        # Keep the same CookieJar object used by the HTTP client.
                        for c in platforms.load_jar(jarfile):session.jar.set_cookie(c)
                        if status==0:return completed()
                        problems.append(stage+': '+log[-1200:])
                        if stop_reason(log):raise PermissionError('Site verification, DRM or rate limit requires attention; '+log[-700:])
                    except PermissionError:raise
                    except Exception as exc:problems.append(stage+': '+diagnostic(exc))
            if not spec.get('enabled',True):break
            print('ZEDENGINE page',flush=True)
            try:
                if force:raise ValueError('Explicit source refresh: using rendered browser discovery')
                from page_media import discover
                found,page_title,jar=discover(source,str(jarfile),spec.get('quality','best'),False)
                for c in jar:session.jar.set_cookie(c)
                if download(found,spec.get('filename') or page_title or title)==0:return completed()
            except Exception as exc:problems.append('page: '+diagnostic(exc));say(problems[-1])
            print('ZEDENGINE browser',flush=True)
            try:
                if not browser_slot:
                    from web_session import browser_download_lease
                    browser_job.enter_context(browser_download_lease(state));browser_slot=True
                from browser_capture import capture
                found,page_title,jar=capture(source,session.jar,state,spec.get('quality','best'),settings,dest)
                if download(found,spec.get('filename') or page_title or title)==0:return completed()
            except Exception as exc:
                problems.append('browser: '+diagnostic(exc));say(problems[-1])
                if any(s in str(exc).lower() for s in ('insufficient','executable doesn\'t exist','no playable media','unrelated or unranked')):break
            if attempt<settings['refresh_attempts']:time.sleep(min(10,2**attempt))
        raise ValueError('All applicable extraction stages failed.\n'+'\n'.join(p[:180]+' … '+p[-320:] if len(p)>520 else p for p in problems[-5:]))
    finally:
        browser_job.close()
        try:session.save()
        except OSError as exc:say('Could not persist website session: '+type(exc).__name__)
        jarfile.unlink(missing_ok=True)


if __name__=='__main__':
    try:raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
    except Exception as exc:print('ERROR '+diagnostic(exc),flush=True);raise SystemExit(1)
