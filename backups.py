"""Streaming tar backups with application-owned retention records."""
import io
import json
import os
import secrets
import sqlite3
import subprocess
import tarfile
import threading
import time
from pathlib import Path
from activities import record
from remotes import relative


def backup(desk,job,options):
    name=options['remote'];root=relative(options.get('path',''));scope=options['scope']
    appid=desk.get_setting('backup_app_id')
    if not appid:appid=secrets.token_hex(16);desk.set_setting('backup_app_id',appid)
    target='/'.join(p for p in (root,'zedair-'+appid,time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+job['id']+'.tar') if p)
    remote=desk.remotes
    args=remote.stream_args(name,target)
    snapshot=desk.state/('.backup-'+job['id']+'.sqlite3')
    sink=None;done=threading.Event();errors=[];success=False
    try:
        if scope in ('system','both'):
            with desk.lock,sqlite3.connect(snapshot) as conn:desk.db.backup(conn)
            snapshot.chmod(0o600)
        if scope in ('ssd','both'):desk.destination('',reserve=False)
        sink=desk.tasks.process(job,args,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        def log():
            for line in sink.stderr:
                errors.append(line.decode('utf-8','replace'));del errors[:-10]
        threading.Thread(target=log,daemon=True).start()
        def watch():
            while not done.wait(.5):
                if desk.stop.is_set() or job['cancel'].is_set():
                    sink.terminate();return
        watcher=threading.Thread(target=watch,daemon=True);watcher.start()
        class Writer:
            def write(self,block):
                desk.tasks.check(job);n=sink.stdin.write(block);job['bytes']+=n
                desk.record_direction('rclone:'+name,0,n);return n
        def add(tar,path,arc):
            desk.tasks.check(job)
            if path.is_symlink() or not path.is_file():return
            before=path.stat();info=tar.gettarinfo(str(path),arcname=arc)
            with path.open('rb') as src:tar.addfile(info,src)
            after=path.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('File changed during backup: '+arc+'; retry while idle')
            job['detail']='Uploading '+arc
        with tarfile.open(fileobj=Writer(),mode='w|',bufsize=64*1024) as tar:
            manifest=json.dumps({'app':'zedair','format':1,'owner':appid,'scope':scope,'created':time.time(),'timezone':'Asia/Kolkata'}).encode()
            info=tarfile.TarInfo('zedair-backup.json');info.size=len(manifest);info.mode=0o600;tar.addfile(info,io.BytesIO(manifest))
            if scope in ('system','both'):
                add(tar,snapshot,'system/state/desk.sqlite3')
                app=Path(__file__).resolve().parent
                for p in sorted(app.iterdir()):
                    if p.is_file() and p.suffix in ('.py','.sh','.md','.txt'):add(tar,p,'system/app/'+p.name)
                for p in (app/'static').rglob('*'):add(tar,p,'system/app/static/'+str(p.relative_to(app/'static')))
                # Private state only; exclude live databases, engine work specs,
                # diagnostics and ephemeral caches. Cookies retain their names.
                for p in desk.state.rglob('*'):
                    rel=p.relative_to(desk.state)
                    if p==snapshot or p.name.startswith('.') or any(x in ('favicon-cache','transmission','engine-releases.json') for x in rel.parts):continue
                    if p.name in ('desk.sqlite3','desk.sqlite3-wal','desk.sqlite3-shm') or p.name.startswith(('video-','profile-','gallery-')):continue
                    if p.suffix in ('.txt','.json','.conf') or 'cookies' in rel.parts:add(tar,p,'system/state/'+rel.as_posix())
                config=json.dumps(desk.config).encode();info=tarfile.TarInfo('system/config.json');info.size=len(config);info.mode=0o600;tar.addfile(info,io.BytesIO(config))
            if scope in ('ssd','both'):
                from storage_tools import visible_files
                for p in visible_files(desk.root):add(tar,p,'ssd/'+p.relative_to(desk.root).as_posix())
        sink.stdin.close();desk.tasks.wait(job,sink);success=True
        desk.write('INSERT INTO backups(remote,path,created,owner) VALUES(?,?,?,?)',(name,target,time.time(),appid))
        cutoff=time.time()-int(options['days'])*86400
        # Only paths recorded after this application's successful uploads. No
        # recursive purge, filename guessing, or deletion of other remotes' data.
        for row in desk.rows('SELECT * FROM backups WHERE owner=? AND remote=? AND created<?',(appid,name,cutoff)):
            desk.tasks.check(job)
            prefix='/'.join(p for p in (root,'zedair-'+appid) if p)+'/'
            if not row['path'].startswith(prefix) or '/' in row['path'][len(prefix):]:continue
            remote.run('deletefile',remote.target(name,row['path']),json_output=False)
            desk.write('DELETE FROM backups WHERE remote=? AND path=?',(name,row['path']))
            record(desk,'backup expired',name+':'+row['path'])
        return {'remote':name,'path':target,'bytes':job['bytes']}
    finally:
        done.set()
        if sink and sink.poll() is None:
            sink.terminate()
            try:sink.wait(timeout=15)
            except subprocess.TimeoutExpired:sink.kill();sink.wait()
        snapshot.unlink(missing_ok=True)
        if sink and not success:
            try:remote.run('deletefile',remote.target(name,target),json_output=False)
            except Exception:pass
