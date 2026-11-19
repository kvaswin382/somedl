"""SSD duplicate inspection, upload publication and bounded thumbnail work."""
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import time
from pathlib import Path
from fileops import guard
from publishing import filename
from activities import record

VIDEO={'.mp4','.mkv','.webm','.mov','.m4v','.avi','.ts'}
IMAGE={'.jpg','.jpeg','.png','.webp'}


def stamp(p):
    s=p.stat();return f'{s.st_size}:{s.st_mtime_ns}'


def visible_files(root):
    for current,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=[n for n in dirs if not n.startswith('.') and not (Path(current)/n).is_symlink() and not os.path.ismount(Path(current)/n)]
        for n in names:
            p=Path(current)/n
            if not n.startswith('.') and p.is_file() and not p.is_symlink():yield p


def identical(a,b,check=lambda:None):
    with a.open('rb') as x,b.open('rb') as y:
        while True:
            check();left=x.read(1024*1024);right=y.read(1024*1024)
            if left!=right:return False
            if not left:return True


def duplicates(desk,job):
    desk.destination('',reserve=False);sizes={};groups=[];count=0
    for p in visible_files(desk.root):
        desk.tasks.check(job);count+=1
        if count>100000:raise ValueError('Scan exceeds 100,000 files; no files were deleted')
        sizes.setdefault(p.stat().st_size,[]).append((p,stamp(p)))
    for size,items in sizes.items():
        if len(items)<2:continue
        hashes={}
        for p,before in items:
            h=hashlib.sha256()
            with p.open('rb') as f:
                while block:=f.read(1024*1024):
                    desk.tasks.check(job);h.update(block);job['bytes']+=len(block)
            if stamp(p)!=before:continue
            hashes.setdefault(h.hexdigest(),[]).append({'path':p.relative_to(desk.root).as_posix(),'stamp':before,'mtime':p.stat().st_mtime})
        for digest,files in hashes.items():
            if len(files)>1:groups.append({'hash':digest,'size':size,'files':files})
        job['detail']=f'Scanning {count} files · {len(groups)} duplicate groups'
    return {'groups':groups,'files':count,'recoverable':sum(g['size']*(len(g['files'])-1) for g in groups)}


def delete_duplicates(desk,data):
    scan=desk.tasks.jobs.get(data.get('scan'))
    if not scan or scan['kind']!='duplicates' or scan['status']!='done':raise ValueError('Run a complete duplicate scan first')
    if data.get('confirm') is not True:raise ValueError('Review and confirm duplicate deletion')
    mode=data.get('keep');manual=data.get('selected',{})
    if mode not in ('oldest','newest','manual'):raise ValueError('Choose a copy to keep')
    plans=[]
    with desk.file_lock:
        # Validate the entire selection and compare actual bytes again before deleting anything.
        for g in scan['result']['groups']:
            files=g['files'];chosen=manual.get(g['hash']) if mode=='manual' else sorted(files,key=lambda f:(f['mtime'],f['path']),reverse=mode=='newest')[0]['path']
            if chosen not in {f['path'] for f in files}:raise ValueError('Select one retained copy in every group')
            keep=desk.destination(chosen,reserve=False)
            for f in files:
                p=desk.destination(f['path'],reserve=False);guard(desk,p,f['stamp'])
                if p!=keep and not identical(keep,p):raise ValueError('Files changed after scan; scan again')
            plans.extend((keep,desk.destination(f['path'],reserve=False),f['stamp']) for f in files if f['path']!=chosen)
        removed=0
        for keep,p,before in plans:
            if stamp(p)!=before or not identical(keep,p):raise ValueError('Files changed during deletion; remaining files preserved')
            p.unlink();removed+=1
        scan['status']='consumed'
    record(desk,'duplicates deleted',str(removed)+' extra copies; one copy per group retained')
    return {'removed':removed}


def delete_batch(desk,data):
    items=data.get('files',[])
    if data.get('confirm') is not True or not isinstance(items,list) or not 1<=len(items)<=100:raise ValueError('Confirm deleting 1–100 files')
    if len({i.get('path') for i in items})!=len(items):raise ValueError('Duplicate file selection')
    with desk.file_lock:
        paths=[]
        for i in items:
            p=desk.destination(i.get('path',''),reserve=False);guard(desk,p,i.get('stamp'));paths.append(p)
        for p in paths:p.unlink()
    record(desk,'files deleted',f'{len(paths)} selected files')
    return {'removed':len(paths)}


def upload(desk,data):
    action=data.get('action');key=data.get('id','')
    with desk.file_lock:
        if action=='start':
            if desk.get_setting('reboot_requested_at'):raise ValueError('Scheduled reboot in progress')
            folder=desk.destination(data.get('folder',''),create=False)
            name=data.get('name','');size=data.get('size')
            if not isinstance(name,str) or not name or name.startswith('.') or '/' in name or '\\' in name or len(name.encode())>200:raise ValueError('Invalid filename')
            if type(size) is not int or size<0 or size>desk.storage()['free']-desk.get_setting('reserve_gib',5)*1024**3:raise ValueError('Insufficient SSD space for upload and reserve')
            if len(desk.tasks.uploads)>=20:raise ValueError('Finish or cancel existing uploads first')
            key=secrets.token_hex(16);temp=folder/('.zedair-upload-'+key)
            with temp.open('xb'):pass
            desk.tasks.uploads[key]={'folder':folder,'temp':temp,'name':name,'size':size,'offset':0,'updated':time.time()}
            record(desk,'upload started',name);return {'id':key,'offset':0}
        item=desk.tasks.uploads.get(key)
        if not item:raise ValueError('Upload expired; start again')
        temp=item['temp'];desk.destination(item['folder'].relative_to(desk.root).as_posix(),reserve=action=='chunk')
        if action=='cancel':
            temp.unlink(missing_ok=True);del desk.tasks.uploads[key];return {'cancelled':True}
        if action=='chunk':
            if data.get('offset')!=item['offset']:raise ValueError('Upload offset mismatch; retry from the acknowledged offset')
            block=base64.b64decode(data.get('data',''),validate=True)
            if not 0<len(block)<=1024*1024 or item['offset']+len(block)>item['size']:raise ValueError('Invalid upload chunk')
            if temp.is_symlink() or temp.stat().st_size!=item['offset']:raise ValueError('Upload file changed')
            with temp.open('ab') as out:out.write(block)
            item['offset']+=len(block);item['updated']=time.time();return {'offset':item['offset']}
        if action!='finish' or item['offset']!=item['size']:raise ValueError('Upload is incomplete')
        with temp.open('ab') as out:out.flush();os.fsync(out.fileno())
        base=Path(item['name']);taken={p.name.casefold() for p in item['folder'].iterdir()}
        for i in range(10000):
            target=item['folder']/(base.stem+(f' ({i})' if i else '')+base.suffix)
            if target.name.casefold() not in taken:break
        else:raise ValueError('Too many filename collisions')
        with target.open('xb'):pass
        os.replace(temp,target);del desk.tasks.uploads[key]
        record(desk,'upload completed',target.relative_to(desk.root).as_posix())
        return {'path':target.relative_to(desk.root).as_posix()}


def set_cover(desk,data):
    with desk.file_lock:
        p=desk.destination(data.get('path',''),reserve=False)
        if p.suffix.lower() not in IMAGE or not p.is_file():raise ValueError('Select an image inside the folder')
        guard(desk,p,data.get('stamp'))
        folder=p.parent.relative_to(desk.root).as_posix()
        desk.write('INSERT OR REPLACE INTO covers VALUES(?,?)',(folder,p.name))
    record(desk,'folder cover changed',folder)
    return {'ok':True}


def thumbnail_path(desk,p):
    key=hashlib.sha256((str(p.relative_to(desk.root))+'\0'+stamp(p)).encode()).hexdigest()
    return desk.root/'.thumbnails'/(key+'.jpg')


def thumbnail(desk,job,p):
    out=thumbnail_path(desk,p)
    if out.exists():return
    out.parent.mkdir(exist_ok=True)
    if out.parent.is_symlink():raise ValueError('Thumbnail folder must not be a symlink')
    before=stamp(p);tmp=out.with_suffix('.tmp.jpg')
    # Extract a cover if present; otherwise select a frame. One size and one CPU thread.
    probe=subprocess.run(['ffprobe','-v','error','-format_whitelist','mov,matroska,webm,avi,mpegts','-protocol_whitelist','file,pipe','-show_streams','-of','json',str(p)],capture_output=True,text=True,timeout=30)
    if probe.returncode:return
    streams=json.loads(probe.stdout).get('streams',[])
    cover=next((s for s in streams if s.get('disposition',{}).get('attached_pic')),None)
    args=['ffmpeg','-nostdin','-v','error','-y','-threads','1','-format_whitelist','mov,matroska,webm,avi,mpegts','-protocol_whitelist','file,pipe','-i',str(p)]
    args+=['-map','0:'+str(cover['index'])] if cover else ['-map','0:v:0']
    args+=['-frames:v','1','-vf','scale=480:270:force_original_aspect_ratio=decrease','-threads','1',str(tmp)]
    proc=desk.tasks.process(job,args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        desk.tasks.wait(job,proc)
        if stamp(p)!=before:raise ValueError('Video changed during thumbnail generation')
        os.replace(tmp,out)
    finally:tmp.unlink(missing_ok=True)
    # MP4 supports an attached cover without re-encoding video/audio. Other
    # containers still get the same browser thumbnail; do not transcode them.
    if not cover and p.suffix.lower() in ('.mp4','.m4v') and desk.storage()['free']>p.stat().st_size+desk.get_setting('reserve_gib',5)*1024**3:
        try:
            with desk.file_lock:guard(desk,p,before)
        except Exception:return
        temp=p.with_name('.cover-'+secrets.token_hex(8)+'.mp4')
        proc=desk.tasks.process(job,['ffmpeg','-nostdin','-v','error','-y','-threads','1','-i',str(p),'-i',str(out),'-map','0','-map','1:v','-c','copy','-disposition:v:'+str(sum(s.get('codec_type')=='video' for s in streams)),'attached_pic','-threads','1',str(temp)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            desk.tasks.wait(job,proc)
            with desk.file_lock:
                guard(desk,p,before)
                os.replace(temp,p)
                os.replace(out,thumbnail_path(desk,p))
        finally:temp.unlink(missing_ok=True)


def scan_thumbnails(desk,job):
    count=0;deadline=time.monotonic()+300
    for p in visible_files(desk.root):
        desk.tasks.check(job)
        if p.suffix.lower() not in VIDEO or thumbnail_path(desk,p).exists():continue
        from web_session import available_memory
        if count>=20 or time.monotonic()>deadline or desk.workers or available_memory()<128*1024**2:break
        if any(t.get('status') in (1,2,3,4,5,6) and p.is_relative_to(Path(t['downloadDir'])) for t in desk.torrents):continue
        try:thumbnail(desk,job,p);count+=1
        except InterruptedError:raise
        except (OSError,ValueError,subprocess.SubprocessError) as exc:record(desk,'thumbnail skipped',p.name+': '+str(exc))
        job['detail']=f'Processed {count} videos'
    return {'videos':count}
