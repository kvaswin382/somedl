"""Crash-resumable, numbered publication on filesystems without hard links (exFAT)."""
import hashlib
import json
import os
import re
import time
from pathlib import Path

MEDIA = {'.mp4','.mkv','.webm','.mov','.avi','.m4v','.mp3','.m4a','.ogg','.opus','.flac','.wav','.aac','.jpg','.jpeg','.png','.webp','.gif','.avif','.srt','.vtt','.ass','.ts','.m4b'}

def filename(title):
    title = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', str(title))
    title = re.sub(r'\s+', ' ', title).strip(' .')
    title = title.encode()[:110].decode('utf-8','ignore').rstrip(' .') or 'Download'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', title, re.I):
        title = '_' + title
    return title

def fingerprint(path):
    size = path.stat().st_size
    with path.open('rb') as stream:
        first = stream.read(65536)
        stream.seek(max(0,size-65536))
        last = stream.read(65536)
    return hashlib.sha256(str(size).encode()+first+last).hexdigest()

def title_for(path):
    for meta in (path.with_suffix('.info.json'),path.with_suffix('.json'),Path(str(path)+'.json')):
        if meta.is_file() and meta.stat().st_size < 16*1024*1024:
            try:
                data=json.loads(meta.read_text())
                value=data.get('title') or data.get('description')
                if value:
                    return filename(value)
            except (ValueError,OSError):
                pass
    return filename(re.sub(r' \[[^\]]+\]$', '', path.stem))

def publish(desk, job, stage, destination):
    with desk.file_lock:
        return _publish(desk, job, stage, destination)

def _publish(desk, job, stage, destination):
    jid=job['id']
    # Journal commits precede rename. A restart in the publish phase never re-downloads.
    for row in desk.rows('SELECT * FROM published WHERE job=? AND complete=0',(jid,)):
        finish(desk,row,stage,destination)
    candidates=sorted(p for p in stage.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in MEDIA)
    occupied={p.name.casefold() for p in destination.iterdir()}
    for source in candidates:
        if desk.rows('SELECT source FROM published WHERE job=? AND source=?',(jid,source.name)):
            continue
        desk.destination(str(destination.relative_to(desk.root)),reserve=False)
        custom=job.get('filename','')
        stem=filename(Path(custom).stem if Path(custom).suffix.lower() in MEDIA else custom) if custom else title_for(source)
        marker=('ZEDAIR-RESERVED:'+jid+':'+source.name).encode()
        for index in range(100000):
            target=destination/(stem+(f' ({index})' if index else '')+source.suffix.lower())
            # Explicit casefold also protects Windows use when tests run on case-sensitive Linux.
            if target.name.casefold() in occupied:
                continue
            try:
                with target.open('xb') as out:
                    out.write(marker); out.flush(); os.fsync(out.fileno())
                occupied.add(target.name.casefold())
                break
            except FileExistsError:
                continue
        else:
            raise ValueError('Too many files with the same title')
        desk.write('INSERT INTO published(job,source,target,signature,complete) VALUES (?,?,?,?,0)',
                   (jid,source.name,target.name,fingerprint(source)))
        finish(desk,{'job':jid,'source':source.name,'target':target.name,'signature':fingerprint(source)},stage,destination)
    if not desk.rows('SELECT source FROM published WHERE job=? AND complete=1',(jid,)):
        raise ValueError('No downloadable media files were produced. Check the link and website cookies.')

def finish(desk,row,stage,destination):
    source=stage/row['source']; target=destination/row['target']
    desk.destination(str(destination.relative_to(desk.root)),reserve=False)
    if source.exists():
        marker=('ZEDAIR-RESERVED:'+row['job']+':'+row['source']).encode()
        if target.is_symlink() or not target.is_file() or target.stat().st_size!=len(marker) or target.read_bytes()!=marker:
            raise ValueError('A reserved output file changed; publication stopped to preserve it')
        os.replace(source,target)
    elif not target.is_file() or target.is_symlink() or fingerprint(target)!=row['signature']:
        raise ValueError('A published output file is missing or changed; inspect the SSD before retrying')
    # Persist completion time separately from the source website's original mtime.
    stat=target.stat();folder=destination.relative_to(desk.root).as_posix()
    if folder=='.':folder=''
    with desk.lock:
        desk.db.execute('INSERT INTO file_dates(folder,name,size,mtime_ns,downloaded) VALUES(?,?,?,?,?) ON CONFLICT(folder,name) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,downloaded=excluded.downloaded',
                        (folder,target.name,stat.st_size,stat.st_mtime_ns,time.time()))
        desk.db.execute('UPDATE published SET complete=1 WHERE job=? AND source=?',(row['job'],row['source']))
        desk.db.commit()
