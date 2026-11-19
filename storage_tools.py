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
from fileops import guard,protection,check_file
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


def start_duplicate_delete(desk,data):
    with desk.lock,desk.tasks.lock:
        scan=desk.tasks.jobs.get(data.get('scan'))
        if not scan or scan['kind']!='duplicates' or scan['status']!='done':raise ValueError('Run a complete duplicate scan first')
        existing=desk.tasks.jobs.get(scan.get('delete_task'))
        if existing and existing['status'] in ('queued','running'):return {'id':existing['id']}
        if data.get('confirm') is not True:raise ValueError('Review and confirm duplicate deletion')
        if data.get('keep') not in ('oldest','newest','manual'):raise ValueError('Choose a copy to keep')
        result=desk.tasks.submit('duplicate-delete',lambda job:delete_duplicates(desk,data,job))
        scan['delete_task']=result['id']
        return result


def delete_duplicates(desk,data,job=None):
    scan=desk.tasks.jobs.get(data.get('scan'))
    if not scan or scan['kind']!='duplicates' or scan['status']!='done':raise ValueError('Run a complete duplicate scan first')
    if data.get('confirm') is not True:raise ValueError('Review and confirm duplicate deletion')
    mode=data.get('keep');manual=data.get('selected',{})
    if mode not in ('oldest','newest','manual'):raise ValueError('Choose a copy to keep')
    job=job if job is not None else {'cancel':__import__('threading').Event(),'bytes':0}
    total=sum(len(g['files'])-1 for g in scan['result']['groups'])
    total_bytes=4*sum(g['size']*(len(g['files'])-1) for g in scan['result']['groups'])
    job.update(total=total,removed=0,bytes=0,total_bytes=total_bytes,progress=0,detail='Checking file ownership')
    def check():desk.tasks.check(job)
    def compare(a,b):
        with a.open('rb') as left,b.open('rb') as right:
            while True:
                check();x=left.read(1024*1024);y=right.read(1024*1024)
                if x!=y:raise ValueError('Files changed after scan; remaining files preserved. Scan again.')
                job['bytes']+=len(x)+len(y)
                job['progress']=min(99,100*job['bytes']/max(1,total_bytes))
                if not x:return
                time.sleep(.001)  # Yield SSD bandwidth to foreground transfers and folder navigation.
    plans=[]
    with desk.file_lock:
        ownership=protection(desk)
        # Prevalidate every group before the first deletion. Never compare names alone.
        for g in scan['result']['groups']:
            check();files=g['files'];chosen=manual.get(g['hash']) if mode=='manual' else sorted(files,key=lambda f:(f['mtime'],f['path']),reverse=mode=='newest')[0]['path']
            if chosen not in {f['path'] for f in files}:raise ValueError('Select one retained copy in every group')
            keep=desk.destination(chosen,reserve=False);keep_stamp=stamp(keep)
            for f in files:
                p=desk.destination(f['path'],reserve=False);guard(desk,p,f['stamp'],ownership)
                if p!=keep:
                    job['detail']='Verifying '+f['path'];compare(keep,p)
                    plans.append((keep,keep_stamp,p,f['stamp']))
        # Refresh ownership once after the potentially long read, not once per file.
        ownership=protection(desk)
        for keep,keep_stamp,p,before in plans:
            check();guard(desk,keep,keep_stamp,ownership);guard(desk,p,before,ownership)
            job['detail']=f"Deleting {job['removed']+1} of {total}: {p.name}"
            compare(keep,p)
            if stamp(keep)!=keep_stamp or stamp(p)!=before:raise ValueError('Files changed during deletion; remaining files preserved')
            check();p.unlink();job['removed']+=1
        forget_dates(desk,[p for _,_,p,_ in plans])
        scan['status']='consumed'
    job.update(progress=100,detail=f"Deleted {job['removed']} extra copies; one copy per group retained")
    record(desk,'duplicates deleted',job['detail'])
    return {'removed':job['removed']}


def forget_dates(desk,paths):
    with desk.lock:
        desk.db.executemany('DELETE FROM file_dates WHERE folder=? AND name=?',
            [('' if p.parent==desk.root else p.parent.relative_to(desk.root).as_posix(),p.name) for p in paths])
        desk.db.commit()


def delete_batch(desk,data):
    items=data.get('files',[])
    if data.get('confirm') is not True or not isinstance(items,list) or not 1<=len(items)<=100:raise ValueError('Confirm deleting 1–100 files')
    if len({i.get('path') for i in items})!=len(items):raise ValueError('Duplicate file selection')
    with desk.file_lock:
        paths=[]
        for i in items:
            p=desk.destination(i.get('path',''),reserve=False);check_file(p,i.get('stamp'));paths.append(p)
        ownership=protection(desk)
        for p,i in zip(paths,items):guard(desk,p,i.get('stamp'),ownership)
        for p in paths:p.unlink()
        forget_dates(desk,paths)
    record(desk,'files deleted',f'{len(paths)} selected files')
    return {'removed':len(paths)}


def upload(desk,data):
    action=data.get('action');key=data.get('id','')
    with desk.file_lock,desk.lock,desk.tasks.lock:
        if action=='start':
            for expired,item in list(desk.tasks.uploads.items()):
                if time.time()-item['updated']>3600:
                    if not item['temp'].is_symlink():item['temp'].unlink(missing_ok=True)
                    del desk.tasks.uploads[expired]
            if desk.get_setting('reboot_requested_at'):raise ValueError('Scheduled reboot in progress')
            folder=desk.destination(data.get('folder',''),create=False)
            name=data.get('name','');size=data.get('size')
            if not isinstance(name,str) or not name or name.startswith('.') or '/' in name or '\\' in name or len(name.encode())>200:raise ValueError('Invalid filename')
            from server import safe_relative
            safe_relative(name)
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
        if target.suffix.lower() in VIDEO:queue_thumbnails(desk,[target],embed=True)
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


def folder_cover(desk,path):
    rows=desk.rows('SELECT name FROM covers WHERE folder=?',(path.relative_to(desk.root).as_posix(),))
    if not rows:return None
    name=rows[0]['name']
    if not isinstance(name,str) or '/' in name or '\\' in name:return None
    p=path/name
    return p if p.is_file() and not p.is_symlink() and p.suffix.lower() in IMAGE else None


def thumbnail_path(desk,p):
    key=hashlib.sha256((str(p.relative_to(desk.root))+'\0'+stamp(p)).encode()).hexdigest()
    return desk.root/'.thumbnails'/(key+'.jpg')


def thumbnail(desk,job,p,embed=True):
    out=thumbnail_path(desk,p)
    if out.exists() and not embed:return
    out.parent.mkdir(exist_ok=True)
    if out.parent.is_symlink():raise ValueError('Thumbnail folder must not be a symlink')
    before=stamp(p);tmp=out.with_suffix('.tmp.jpg')
    # Extract a cover if present; otherwise select a frame. One size and one CPU thread.
    probe=subprocess.run(low_priority(['ffprobe','-threads','1','-v','error','-format_whitelist','mov,matroska,webm,avi,mpegts','-protocol_whitelist','file,pipe','-show_streams','-of','json',str(p)]),capture_output=True,text=True,timeout=15)
    if probe.returncode:raise ValueError('FFprobe could not read this video')
    streams=json.loads(probe.stdout).get('streams',[])
    cover=next((s for s in streams if s.get('disposition',{}).get('attached_pic')),None)
    if not out.exists():
        args=['ffmpeg','-nostdin','-v','error','-y','-filter_threads','1','-threads','1','-format_whitelist','mov,matroska,webm,avi,mpegts','-protocol_whitelist','file,pipe','-i',str(p)]
        args+=['-map','0:'+str(cover['index'])] if cover else ['-map','0:v:0']
        args+=['-frames:v','1','-vf','scale=480:270:force_original_aspect_ratio=decrease','-threads','1',str(tmp)]
        proc=desk.tasks.process(job,low_priority(args),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            desk.tasks.wait(job,proc,timeout=60)
            if stamp(p)!=before:raise ValueError('Video changed during thumbnail generation')
            os.replace(tmp,out)
        finally:tmp.unlink(missing_ok=True)
    # MP4 supports an attached cover without re-encoding video/audio. Other
    # containers still get the same browser thumbnail; do not transcode them.
    if embed and not cover and p.suffix.lower() in ('.mp4','.m4v') and desk.storage()['free']>p.stat().st_size+desk.get_setting('reserve_gib',5)*1024**3:
        try:
            with desk.file_lock:guard(desk,p,before)
        except Exception:return
        temp=p.with_name('.cover-'+secrets.token_hex(8)+'.mp4')
        proc=desk.tasks.process(job,low_priority(['ffmpeg','-nostdin','-v','error','-y','-threads','1','-i',str(p),'-i',str(out),'-map','0','-map','1:v','-c','copy','-disposition:v:'+str(sum(s.get('codec_type')=='video' for s in streams)),'attached_pic','-threads','1',str(temp)]),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            desk.tasks.wait(job,proc)
            with desk.file_lock:
                guard(desk,p,before)
                import shutil
                shutil.copystat(p,temp)
                os.replace(temp,p)
                folder=p.parent.relative_to(desk.root).as_posix();folder='' if folder=='.' else folder
                desk.write('UPDATE file_dates SET size=?,mtime_ns=? WHERE folder=? AND name=?',(p.stat().st_size,p.stat().st_mtime_ns,folder,p.name))
                os.replace(out,thumbnail_path(desk,p))
        finally:temp.unlink(missing_ok=True)


def low_priority(args):
    import shutil
    prefix=[]
    if shutil.which('nice'):prefix+=['nice','-n','10']
    if shutil.which('ionice'):prefix+=['ionice','-c','3']
    return prefix+args


def thumbnail_status(desk,p):
    if thumbnail_path(desk,p).is_file():return 'ready'
    failed=getattr(desk,'thumbnail_failures',{}).get(str(p))
    return 'unavailable' if failed and failed[0]==stamp(p) and failed[1]>time.monotonic() else 'pending'


def scan_thumbnails(desk,job,paths=None,embed=False):
    count=0;deadline=time.monotonic()+60
    failures=getattr(desk,'thumbnail_failures',None)
    if failures is None:failures={};desk.thumbnail_failures=failures
    for p in paths if paths is not None else visible_files(desk.root):
        desk.tasks.check(job)
        from web_session import available_memory
        if count>=20 or time.monotonic()>deadline or (paths is None and desk.workers) or available_memory()<128*1024**2:break
        try:
            if p.suffix.lower() not in VIDEO or not p.is_file():continue
            state=thumbnail_status(desk,p)
            if state!='pending' and not (embed and state=='ready'):continue
            p=desk.destination(p.relative_to(desk.root).as_posix(),reserve=False)
            if any(t.get('status') in (1,2,3,4,5,6) and p.is_relative_to(Path(t['downloadDir'])) for t in desk.torrents):continue
            count+=1;job['detail']='Creating preview: '+p.name
            thumbnail(desk,job,p,embed=embed)
        except InterruptedError:raise
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            try:failures[str(p)]=(stamp(p),time.monotonic()+86400)
            except OSError:pass
            if len(failures)>2000:failures.pop(next(iter(failures)))
            record(desk,'thumbnail skipped',p.name+': '+str(exc))
    return {'videos':count}


def queue_thumbnails(desk,paths=None,embed=False):
    with desk.lock,desk.tasks.lock:
        pending=getattr(desk,'thumbnail_pending',None)
        if pending is None:pending={};desk.thumbnail_pending=pending
        if paths is not None:
            for p in paths:
                if len(pending)>=200:break
                if p.suffix.lower() in VIDEO:pending[str(p)]=pending.get(str(p),False) or embed
            if not pending:return
        if any(j['kind']=='thumbnails' and j['status'] in ('queued','running') for j in desk.tasks.jobs.values()):return
        def work(job):
            # Bound each task so a corrupt video or large folder cannot monopolize maintenance.
            deadline=time.monotonic()+60;processed=0
            while processed<20 and time.monotonic()<deadline:
                with desk.tasks.lock:
                    if not pending:break
                    key=next(iter(pending));attach=pending.pop(key)
                scan_thumbnails(desk,job,[Path(key)],embed=attach);processed+=1
                desk.tasks.check(job)
            return scan_thumbnails(desk,job) if paths is None and processed==0 else {'videos':processed}
        try:desk.tasks.submit('thumbnails',work)
        except ValueError:pass
