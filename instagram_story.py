"""Explicit Instagram stories/highlights; uses gallery-dl's maintained extractors."""
import base64
import binascii
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


def target(value,mode):
    if mode not in ('stories','highlights'):raise ValueError('Choose stories or highlights')
    value=value.strip().lstrip('@')
    if '://' in value:
        p=urlsplit(value)
        if p.scheme!='https' or p.hostname not in ('instagram.com','www.instagram.com') or p.username or p.password or p.port not in (None,443):raise ValueError('Use an Instagram HTTPS URL')
        parts=p.path.strip('/').split('/')
        if parts[:2]==['stories','highlights'] and len(parts)>=3 and parts[2].isdigit():
            return '', 'https://www.instagram.com/stories/highlights/'+parts[2]+'/'
        if parts[0]=='s' and len(parts)==2 and re.fullmatch(r'aGlnaGxpZ2h0[A-Za-z0-9_=-]+',parts[1]):
            try:
                token=parts[1].rstrip('=')
                decoded=base64.b64decode(token+'='*((-len(token))%4),altchars=b'-_',validate=True).decode('ascii')
            except (ValueError,UnicodeError,binascii.Error):
                raise ValueError('Invalid Instagram highlight share token') from None
            match=re.fullmatch(r'highlight:(\d{1,30})',decoded)
            if not match:raise ValueError('This share link does not identify an Instagram highlight')
            return '', 'https://www.instagram.com/stories/highlights/'+match[1]+'/'
        if parts[0]=='stories' and len(parts)>=2:value=parts[1]
        elif len(parts)<=2 and (len(parts)==1 or parts[1]=='highlights'):value=parts[0]
        else:raise ValueError('Use a username, profile, story or highlight URL')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.]{0,29}',value) or value.lower() in ('me','highlights'):raise ValueError('Enter a specific Instagram username')
    value=value.lower()
    return value, ('https://www.instagram.com/stories/'+value+'/' if mode=='stories' else 'https://www.instagram.com/'+value+'/highlights/')


def run(spec):
    from gallery_dl import config,extractor,job
    dest=Path(spec['dest']);settings=spec['settings']
    config.clear()
    config._config.update({'extractor':{'base-directory':str(dest),'directory':[],
        'filename':'{post_id}_{num}.{extension}', 'cookies':spec['cookie'], 'cookies-update':True,
        'archive':str(dest/'.stories-archive.sqlite3'),'archive-event':['file'],
        'postprocessors':[{'name':'metadata','mode':'json'}],
        'retries':2,'timeout':settings.get('http_timeout',30),
        'instagram':{'videos':'dash','previews':False,'sleep-request':'10-20','sleep-download':'2-5'}},
        'downloader':{'http':{'rate':str(settings.get('media_kib',0))+'k' if settings.get('media_kib') else None},
                      'ytdl':{'options':{'format':'bv*+ba/b','concurrent_fragment_downloads':1}}}})
    # Normalize again for jobs queued before the share-link fix.
    url=spec['url']
    if urlsplit(url).path.startswith('/s/'):
        _,url=target(url,'highlights')
    ext=extractor.find(url)
    if ext is None:raise ValueError('Installed gallery-dl does not recognize this story/highlight URL')
    class Download(job.DownloadJob):
        def initialize(self,kwdict=None):
            super().initialize(kwdict)
            self.hooks.setdefault('after',[]).append(lambda path:print('ZEDITEM story',flush=True))
        def handle_directory(self,kwdict):
            owner=(kwdict.get('user') or {}).get('username') or kwdict.get('username')
            if owner and re.fullmatch(r'[A-Za-z0-9_.]{1,30}',str(owner)):print('ZEDOWNER '+owner.lower(),flush=True)
            return super().handle_directory(kwdict)
    result=Download(ext).run()
    if result:return result
    if not any(p.is_file() and p.suffix.lower() in ('.jpg','.png','.webp','.mp4','.webm','.avif') for p in dest.iterdir()):
        raise ValueError('No currently accessible story/highlight media was returned. Stories may have expired, or Instagram may require a valid session.')
    return 0


if __name__=='__main__':
    try:raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
    except Exception as exc:print('ERROR Instagram stories: '+str(exc),flush=True);raise SystemExit(1)
