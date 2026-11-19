"""Real rclone to an isolated WebDAV fixture, uploads/thumbnails and tar backups.
The fixture's local folder simulates the REMOTE endpoint, never app staging.
"""
import contextlib
import http.server
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Desk
from backups import backup
from storage_tools import thumbnail
import remote_download

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);cloud=root/'remote-endpoint';cloud.mkdir();source=root/'source.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=blue:size=96x64:duration=1','-c:v','libx264','-threads','1',str(source)],check=True)
    class DAV(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def target(self):return cloud/urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).lstrip('/')
        def reply(self,code,body=b'',kind='text/plain'):
            self.send_response(code);self.send_header('Content-Length',str(len(body)));self.send_header('Content-Type',kind);self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path=='/slow.bin':
                self.send_response(200);self.send_header('Content-Length',str(4*1024*1024));self.send_header('Content-Type','application/octet-stream');self.end_headers()
                try:
                    for _ in range(256):self.wfile.write(b'x'*16384);self.wfile.flush();time.sleep(.01)
                except (BrokenPipeError,ConnectionResetError):pass
                return
            if self.path=='/source.mp4':return self.reply(200,source.read_bytes(),'video/mp4')
            p=self.target();self.reply(200,p.read_bytes()) if p.is_file() else self.reply(404)
        def do_HEAD(self):
            p=source if self.path=='/source.mp4' else self.target();self.send_response(200 if p.exists() else 404);self.send_header('Content-Length',str(p.stat().st_size if p.is_file() else 0));self.end_headers()
        def do_MKCOL(self):self.target().mkdir(parents=True,exist_ok=True);self.reply(201)
        def do_DELETE(self):self.target().unlink(missing_ok=True);self.reply(204)
        def do_PUT(self):
            p=self.target();p.parent.mkdir(parents=True,exist_ok=True)
            with p.open('wb') as f:
                if self.headers.get('Transfer-Encoding')=='chunked':
                    while True:
                        n=int(self.rfile.readline().strip(),16)
                        if n==0:self.rfile.readline();break
                        f.write(self.rfile.read(n));self.rfile.read(2)
                else:
                    left=int(self.headers.get('Content-Length',0))
                    while left:
                        b=self.rfile.read(min(left,65536));f.write(b);left-=len(b)
            self.reply(201)
        def do_PROPFIND(self):
            self.rfile.read(int(self.headers.get('Content-Length',0)))
            p=self.target()
            if not p.exists():return self.reply(404)
            items=[p]+list(p.iterdir()) if p.is_dir() and self.headers.get('Depth')!='0' else [p]
            body='<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
            for q in items:
                path='/'+q.relative_to(cloud).as_posix().removeprefix('.')
                if q.is_dir() and not path.endswith('/'):path+='/'
                body+=f'<d:response><d:href>{urllib.parse.quote(path)}</d:href><d:propstat><d:prop><d:resourcetype>{"<d:collection/>" if q.is_dir() else ""}</d:resourcetype><d:getcontentlength>{q.stat().st_size if q.is_file() else 0}</d:getcontentlength><d:getlastmodified>Thu, 19 Nov 2026 00:00:00 GMT</d:getlastmodified></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
            self.reply(207,(body+'</d:multistatus>').encode(),'application/xml')
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),DAV);threading.Thread(target=server.serve_forever,daemon=True).start()
    (root/'ssd/Zedair').mkdir(parents=True)
    desk=Desk({'state':str(root/'state'),'mount':str(root/'ssd'),'require_mount':False});desk.set_setting('reserve_gib',0);desk.rpc.call=lambda *a,**k:{'torrents':[]}
    desk.remotes.config.write_text(f'[cloud]\ntype = webdav\nurl = http://127.0.0.1:{server.server_port}/\nvendor = other\n')
    try:
        desk.remotes.mkdir('cloud','Videos/Nested');assert desk.remotes.browse('cloud','Videos')['folders']==['Nested']
        # Known-length transfer: exact bytes on endpoint, no media in SSD/state.
        with patch('server.validate_url'),patch('download_http.validate_url'),patch('remote_download.validate_url'):
            jid=desk.add_jobs({'urls':[f'http://127.0.0.1:{server.server_port}/source.mp4'],'remote':'cloud','remote_path':'Videos/Nested'})['ids'][0]
            desk.run_job(desk.job(jid))
        assert desk.job(jid)['status']=='done',desk.job(jid)
        files=list((cloud/'Videos/Nested').iterdir());assert len(files)==1 and files[0].read_bytes()==source.read_bytes()
        assert list(desk.root.iterdir())==[],list(desk.root.iterdir())
        assert not list(desk.state.rglob('*.mp4'))
        with patch('server.validate_url'),patch('download_http.validate_url'),patch('remote_download.validate_url'):
            cancelled=desk.add_jobs({'urls':[f'http://127.0.0.1:{server.server_port}/slow.bin'],'remote':'cloud','remote_path':'Videos/Nested'})['ids'][0]
            worker=threading.Thread(target=desk.run_job,args=(desk.job(cancelled),));worker.start()
            deadline=time.monotonic()+10
            while cancelled not in desk.processes and worker.is_alive() and time.monotonic()<deadline:time.sleep(.01)
            desk.job_action(cancelled,'pause');worker.join(timeout=15)
            assert not worker.is_alive(),'Cancelled cloud worker did not exit'
            assert desk.job(cancelled)['status']=='paused'
        assert list((cloud/'Videos/Nested').iterdir())==files,'Cancelled attempt left an object or removed a completed file'
        # Thumbnail cache + attached MP4 cover without changing the video codec.
        video=desk.root/'uploaded.mp4';video.write_bytes(source.read_bytes());key=desk.tasks.submit('thumbnails',lambda j:None)['id'];j=desk.tasks.jobs[key]
        before=video.read_bytes()
        thumbnail(desk,j,video,embed=False)
        assert video.read_bytes()==before,'Generating a file-browser preview rewrote the existing video'
        thumbnail(desk,j,video,embed=True)
        data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(video)]))
        assert any(s.get('disposition',{}).get('attached_pic') for s in data['streams'])
        assert next(s for s in data['streams'] if s['codec_name']=='h264')['width']==96
        assert len(list((desk.root/'.thumbnails').glob('*.jpg')))==1
        features=desk.remotes.run('backend','features','cloud:')['Features']
        if features.get('PutStream'):
            key=desk.tasks.submit('backup',lambda j:None)['id'];j=desk.tasks.jobs[key]
            desk.set_setting('backup_app_id','fixture-owner')
            old='Backups/zedair-fixture-owner/old.tar';foreign='Backups/other-application.tar'
            for item in (old,foreign):
                p=cloud/item;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'old backup')
            desk.write('INSERT INTO backups VALUES(?,?,?,?)',('cloud',old,time.time()-86400*40,'fixture-owner'))
            desk.write('INSERT INTO backups VALUES(?,?,?,?)',('cloud',foreign,time.time()-86400*40,'other-owner'))
            result=backup(desk,j,{'remote':'cloud','path':'Backups','scope':'both','days':30})
            with tarfile.open(cloud/result['path']) as tar:
                assert 'ssd/uploaded.mp4' in tar.getnames();assert 'system/state/desk.sqlite3' in tar.getnames();assert 'zedair-backup.json' in tar.getnames()
            assert len(desk.rows('SELECT * FROM backups'))==2
            assert not (cloud/old).exists();assert (cloud/foreign).read_bytes()==b'old backup'
        else:
            with contextlib.suppress(ValueError):desk.remotes.stream_args('cloud','unknown.mp4')
        print('PASS: real rclone/WebDAV exact-byte pipe, remote folder browsing, no SSD/state media staging, one thumbnail + embedded cover, supported streamed backup.')
    finally:desk.close();server.shutdown();server.server_close()
