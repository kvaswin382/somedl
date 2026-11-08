"""Small resumable gallery-dl worker. Checkpoint only after a fully handled page."""
import json
import logging
import os
import sys
import time
from pathlib import Path

class PageYield(BaseException):pass
class MediaFailure(BaseException):pass

def save(path,data):
    temp=path.with_suffix('.tmp')
    with temp.open('w') as out:
        json.dump(data,out);out.flush();os.fsync(out.fileno())
    os.replace(temp,path)

def validate_connection(data):
    if not isinstance(data,dict):raise ValueError('Instagram returned an invalid feed; collection remains incomplete')
    if 'edges' not in data:
        matches=[value for value in data.values() if isinstance(value,dict) and 'edges' in value]
        if len(matches)!=1:raise ValueError('Instagram did not expose a feed; collection remains incomplete')
        data=matches[0]
    info=data.get('page_info')
    if not isinstance(data['edges'],list) or not isinstance(info,dict) or type(info.get('has_next_page')) is not bool:
        raise ValueError('Instagram pagination metadata is missing; collection remains incomplete')
    if info['has_next_page'] and not info.get('end_cursor'):raise ValueError('Instagram omitted its next page cursor')
    return not info['has_next_page']


def install_checked_pagination(ext,state):
    original_init=ext._init
    def initialize():
        original_init()
        api=getattr(ext,'api',None)
        if api is None or not hasattr(api,'_pagination_graphql'):return
        original_page=api._pagination_graphql
        def checked(opname,variables,fieldname,path=''):
            original_request=ext.request;ended=False
            def request(*args,**kwargs):
                nonlocal ended
                response=original_request(*args,**kwargs)
                if args and '/graphql' in args[0] and isinstance(kwargs.get('data'),dict) and kwargs['data'].get('fb_api_req_friendly_name')==opname:
                    payload=json.loads(response.text)
                    try:
                        ended=validate_connection(payload.get('data',{}).get(fieldname))
                        state['verified_end']=ended
                    except ValueError:
                        # Structural feed errors can use the authenticated compatibility feed.
                        # Authentication, challenge and rate-limit responses never trigger this.
                        from platforms import failure_kind
                        errors=payload.get('errors') or []
                        reauth=any(isinstance(e,dict) and e.get('requires_reauth') for e in errors) if isinstance(errors,list) else False
                        if not reauth and failure_kind(str(errors))[0]=='transient':state['feed_error']=True
                        raise
                return response
            ext.request=request
            try:
                yield from original_page(opname,variables,fieldname,path)
                if not ended:raise ValueError('Instagram feed ended without an explicit final page; collection remains incomplete')
            finally:ext.request=original_request
        api._pagination_graphql=checked
    ext._init=initialize


def compatibility_posts(ext,state,feed):
    """Authenticated feed alternative for structurally broken web queries, not access blocks."""
    info=ext.api._call('/v1/users/web_profile_info/',params={'username':ext.item})
    user=info.get('data',{}).get('user',{})
    identifier=str(user.get('id') or user.get('pk') or '')
    if not identifier.isdigit():raise ValueError('Instagram compatibility feed did not return a profile ID')
    cursor=state.get('cursor')
    while True:
        params={'count':12}
        if cursor:params['max_id']=cursor
        data=ext.api._call('/v1/feed/user/'+identifier+'/',params=params)
        if not isinstance(data.get('items'),list) or type(data.get('more_available')) is not bool:
            raise ValueError('Instagram compatibility feed omitted pagination metadata; collection remains incomplete')
        for post in data['items']:
            if feed!='photos' or not ext._is_reel(post):yield post
        if not data['more_available']:
            ext._update_cursor(None);return
        cursor=data.get('next_max_id')
        if not cursor:raise ValueError('Instagram compatibility feed omitted its next cursor')
        ext._update_cursor(cursor)


def feeds_for(mode,images_only):
    if mode=='avatar':return ['avatar']
    if images_only:return ['posts'] if mode=='everything' else ['photos']
    return {'posts':['photos'],'reels':['reels'],'everything':['posts','reels']}[mode]


def run(spec, slice_seconds=900):
    from gallery_dl import config,extractor,job
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    conf=spec['config'];dest=Path(spec['dest']);checkpoint=dest/'.profile-checkpoint-v1.json'
    progress=json.loads(checkpoint.read_text()) if checkpoint.exists() else {'feeds':{},'pages':0}
    feeds=feeds_for(spec['mode'],spec['images_only'])
    deadline=time.monotonic()+slice_seconds
    for feed in feeds:
        state=progress['feeds'].setdefault(feed,{'cursor':None,'done':False,'posts':0})
        if state['done']:continue
        config.clear();config._config.update(conf)
        ig=config._config['extractor']['instagram']
        ig['cursor']=state['cursor'] or True
        print('ZEDPROFILE '+('Resuming' if state['cursor'] else 'Requesting')+' '+feed+' feed; slow collection with saved page checkpoints',flush=True)
        ext=extractor.find(spec['url'].rstrip('/')+'/'+feed+'/')
        if ext is None or not hasattr(ext,'_update_cursor'):raise RuntimeError('Installed gallery-dl does not expose the supported Instagram pagination interface; update engines')
        class Download(job.DownloadJob):
            def initialize(self,kwdict=None):
                super().initialize(kwdict)
                self.hooks.setdefault('after',[]).append(lambda path:print('ZEDITEM '+str(path.kwdict.get('media_id','saved')),flush=True))
            def handle_directory(self,kwdict):
                state['posts']+=1
                print('ZEDPROFILE Processing '+feed+' post; '+str(progress['pages'])+' pages checkpointed',flush=True)
                return super().handle_directory(kwdict)
            def handle_url(self,url,kwdict):
                super().handle_url(url,kwdict)
                if self.status:raise MediaFailure()
        if hasattr(ext,'_parse_post'):
            original_parse=ext._parse_post
            def parse_post(post):
                parsed=original_parse(post)
                expected=len(post.get('carousel_media') or [post])
                if len(parsed.get('_files',[]))<expected:
                    raise ValueError('Instagram omitted media from a post/carousel; page kept incomplete for retry')
                return parsed
            ext._parse_post=parse_post
        install_checked_pagination(ext,state)
        if state.get('backend')=='compatibility' and feed in ('posts','photos'):
            ext.posts=lambda:compatibility_posts(ext,state,feed)
        download=Download(ext)
        original=ext._update_cursor
        last_cursor=state['cursor']
        def checkpoint_page(cursor):
            nonlocal last_cursor
            if download.status:raise MediaFailure()
            if cursor and cursor==last_cursor:raise RuntimeError('Instagram repeated its page cursor; collection is incomplete')
            result=original(cursor)
            state['cursor']=cursor;progress['pages']+=1;last_cursor=cursor
            save(checkpoint,progress)
            print('ZEDPROFILE Page checkpoint saved; '+str(progress['pages'])+' pages processed',flush=True)
            if cursor and time.monotonic()>=deadline:raise PageYield()
            return result
        ext._update_cursor=checkpoint_page
        try:status=download.run()
        except PageYield:return 75
        except MediaFailure:return 1
        if (state.get('feed_error') or (not status and state['posts']==0)) and feed in ('posts','photos') and state.get('backend')!='compatibility':
            state.update(backend='compatibility',cursor=None,posts=0,feed_error=False)
            save(checkpoint,progress)
            print('ZEDPROFILE Web feed returned no usable pagination. Resting before trying the authenticated compatibility feed; saved media IDs are retained.',flush=True)
            return 75
        if status:return status
        if state['posts']==0 and not (feed=='reels' and state.get('verified_end')):
            print('ERROR No posts were returned by Instagram. Collection is incomplete; not marked finished.',flush=True)
            return 76
        state['done']=True;save(checkpoint,progress)
        if time.monotonic()>=deadline and feed!=feeds[-1]:return 75
    if spec['images_only'] and not any(dest.glob('*.jpg')) and not any(dest.glob('*.png')) and not any(dest.glob('*.webp')):
        import sqlite3
        archive=dest/'.profile-downloads-v2.sqlite3'
        if not archive.exists():return 79
        with sqlite3.connect(archive) as db:
            if db.execute('SELECT count(*) FROM archive').fetchone()[0]==0:return 79
    print('ZEDPROFILE Available feeds exhausted. Collection finished; website completeness is not independently verifiable.',flush=True)
    return 0

if __name__=='__main__':
    try:raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
    except Exception as exc:
        print('ERROR Instagram collection: '+str(exc),flush=True);raise SystemExit(1)
