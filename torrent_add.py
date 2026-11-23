"""Serial torrent submission and explicit, non-destructive fresh-copy confirmation."""
import base64
import re
import secrets
import time
from pathlib import Path
import engine_settings
from publishing import filename
from activities import record


def ready(desk):
    from server import APIError
    if desk.get_setting('hold',False):raise APIError('Resume the queue before adding a torrent',409)
    desk.destination('',reserve=True)


def torrent(desk,key):
    from server import APIError
    items=desk.rpc.call('torrent-get',{'ids':[key],'fields':['hashString','name','downloadDir']}).get('torrents',[])
    if len(items)!=1 or not desk.owned_torrent(items[0]):raise APIError('Existing torrent is unavailable or is outside Zedair',409)
    return items[0]


def add(desk,data):
    from server import APIError
    paused=data.get('paused',False)
    if type(paused) is not bool:raise APIError('Paused must be true or false')
    arguments={'paused':True,'peer-limit':desk.get_setting('torrent_peers_per_torrent',engine_settings.DEFAULTS['torrent_peers_per_torrent'])}
    if data.get('magnet'):
        magnet=data['magnet']
        if data.get('metainfo'):raise APIError('Choose torrent files or a magnet link, not both')
        if not isinstance(magnet,str) or len(magnet)>16000 or not magnet.startswith('magnet:?') or 'xt=urn:btih:' not in magnet:raise APIError('Use a valid BitTorrent v1/hybrid magnet link')
        arguments['filename']=magnet
    else:
        meta=data.get('metainfo','')
        if not isinstance(meta,str) or len(meta)>4*((8*1024*1024+2)//3):raise APIError('Torrent file exceeds 8 MiB')
        decoded=base64.b64decode(meta,validate=True)
        if not 1<=len(decoded)<=8*1024*1024 or not decoded.startswith(b'd'):raise APIError('Invalid or oversized .torrent file')
        arguments['metainfo']=meta
    with desk.file_lock:
        ready(desk)
        folder=desk.destination(data.get('folder',''),create=True)
        arguments['download-dir']=str(folder)
        result=desk.rpc.call('torrent-add',arguments)
        item=result.get('torrent-duplicate') or result.get('torrent-added')
        if not isinstance(item,dict) or not re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})',str(item.get('hashString',''))):raise APIError('Torrent engine returned no valid torrent identifier',502)
        key=item['hashString']
        if 'torrent-duplicate' not in result:
            if not paused:
                ready(desk);desk.rpc.call('torrent-start',{'ids':[key]})
            return {'status':'added','paused':paused,'hash':key,'name':item.get('name','Torrent')}
        # Duplicate additions never stop, move, restart or overwrite an existing torrent.
        existing=torrent(desk,key)
        confirmations=desk.torrent_confirmations
        for token,pending in list(confirmations.items()):
            if pending['expires']<time.time():del confirmations[token]
        if len(confirmations)>=100:raise APIError('Too many pending duplicate confirmations; try again later',429)
        token=secrets.token_hex(24)
        proposed=folder/(filename(existing.get('name','Torrent'))+' - copy '+secrets.token_hex(4))
        relative=proposed.relative_to(desk.root).as_posix()
        desk.destination(relative,reserve=False)
        confirmations[token]={'hash':key,'old_folder':str(Path(existing['downloadDir']).resolve()),'folder':relative,
                              'paused':paused,'expires':time.time()+1800}
        return {'status':'duplicate','token':token,'name':existing.get('name','Torrent'),'folder':relative,
                'old_folder':Path(existing['downloadDir']).resolve().relative_to(desk.root.resolve()).as_posix(),'paused':paused}


def fresh_copy(desk,data):
    from server import APIError
    if data.get('confirm') is not True:raise APIError('Confirm downloading a fresh copy first')
    token=data.get('token')
    if not isinstance(token,str):raise APIError('Invalid duplicate confirmation')
    with desk.file_lock:
        pending=desk.torrent_confirmations.get(token)
        if not pending or pending['expires']<time.time():raise APIError('Confirmation expired; submit this torrent again',409)
        if 'result' in pending:return pending['result']
        if pending.get('failed'):raise APIError('This attempt failed. Check the torrent location before submitting again.',409)
        ready(desk)
        key=pending['hash'];existing=torrent(desk,key)
        if str(Path(existing['downloadDir']).resolve())!=pending['old_folder']:raise APIError('Torrent location changed; submit it again to review a fresh confirmation',409)
        folder=desk.destination(pending['folder'])
        folder.mkdir()  # An exclusive empty directory: never reuse or overwrite a previous copy.
        try:
            desk.rpc.call('torrent-stop',{'ids':[key]})
            desk.rpc.call('torrent-set-location',{'ids':[key],'location':str(folder),'move':False})
            desk.rpc.call('torrent-verify',{'ids':[key]})
            if not pending['paused']:
                ready(desk);desk.rpc.call('torrent-start',{'ids':[key]})
        except Exception as exc:
            pending['failed']=True
            try:desk.rpc.call('torrent-stop',{'ids':[key]})
            except Exception:pass
            raise APIError('Fresh-copy setup failed; old files were kept. Check the torrent location and status before retrying: '+str(exc),502) from exc
        result={'status':'added','fresh':True,'paused':pending['paused'],'hash':key,'folder':pending['folder']}
        pending['result']=result
        record(desk,'torrent fresh copy',pending['folder'])
        return result


def dismiss(desk,data):
    token=data.get('token')
    if isinstance(token,str):
        with desk.file_lock:desk.torrent_confirmations.pop(token,None)
    return {'status':'skipped'}
