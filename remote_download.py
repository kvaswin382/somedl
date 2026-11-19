"""Direct source -> bounded pipe -> rclone. No local media file, VFS or fallback."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
import download_http as http
from media import validate_url,video_format
from notifications import sanitized
from publishing import filename
from remotes import Remotes,relative
from web_session import Session


def _run(desk,job):
    jid=job['id'];settings=desk.settings();remotes=desk.remotes
    session=Session(desk.state,job['url'],str(desk.cookie_for(job) or ''),settings['auto_cookies'])
    source=job['url'];validate_url(source)
    client=http.opener(session.jar);info=None;producer=None;sink=None;errors=[]
    output=None;completed=False;response=None;readers=[]
    def stopped():return desk.stop.is_set() or desk.get_setting('hold',False) or desk.job(jid)['status']!='running'
    def lines(pipe):
        for raw in iter(pipe.readline,b''):
            line=raw.decode('utf-8','replace');errors.append(sanitized(line));del errors[:-30]
    def terminate(p):
        if p and p.poll() is None:
            os.killpg(p.pid,signal.SIGTERM)
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
    try:
        try:info=http.inspect(client,source,timeout=settings['http_timeout'])
        except Exception:pass
        direct=info and info['kind'] in ('image','file','video','audio')
        title=job.get('filename') or Path(urllib.parse.urlsplit(source).path).name or 'Download'
        size=None;ffargs=None;response=None
        if direct:
            response=http.request(client,info['url'],info.get('headers'),timeout=settings['http_timeout'])
            length=response.headers.get('Content-Length');size=int(length) if length and length.isdigit() else None
            ext=info.get('ext') or Path(urllib.parse.urlsplit(info['url']).path).suffix or '.bin'
            if not ext.startswith('.'):ext='.'+ext
            name=filename(Path(title).stem)+ext
        else:
            # Extract metadata only. This never invokes a downloader or creates media files.
            import yt_dlp
            options={'quiet':True,'no_warnings':True,'skip_download':True,'noplaylist':True,'cachedir':False,
                     'format':video_format(job['quality'],job.get('container')=='mp4'),'socket_timeout':settings['http_timeout'],'retries':2}
            cookie=desk.cookie_for(job)
            if cookie:options['cookiefile']=str(cookie)
            with yt_dlp.YoutubeDL(options) as ydl:metadata=ydl.extract_info(source,download=False)
            if metadata.get('entries'):raise ValueError('Remote streaming accepts one media item at a time; submit individual URLs')
            streams=metadata.get('requested_formats') or [metadata]
            if any(s.get('has_drm') for s in streams):raise ValueError('DRM-protected media cannot be downloaded')
            ffargs=['ffmpeg','-nostdin','-hide_banner','-loglevel','warning','-threads','1']
            for s in streams:
                validate_url(s['url']);headers=s.get('http_headers',metadata.get('http_headers',{}))
                header=''.join(k+': '+v+'\r\n' for k,v in headers.items() if isinstance(v,str) and '\r' not in v and '\n' not in v and k.lower() in ('user-agent','referer','origin','cookie','authorization'))
                if header:ffargs+=['-headers',header]
                if str(s.get('protocol','')).startswith('m3u8'):ffargs+=['-live_start_index','0']
                ffargs+=['-protocol_whitelist','http,https,tcp,tls,crypto','-i',s['url']]
            if len(streams)>1:ffargs+=['-map','0:v:0','-map','1:a:0']
            else:ffargs+=['-map','0:v:0?','-map','0:a:0?']
            ffargs+=['-c','copy','-threads','1','-movflags','frag_keyframe+empty_moov+default_base_moof','-f','mp4','pipe:1']
            name=filename(Path(job.get('filename') or metadata.get('title') or 'Video').stem)+'.mp4'
        # A per-job suffix prevents retries/collisions from overwriting existing user files.
        base=Path(name);output=relative('/'.join(x for x in (job.get('remote_path',''),base.stem+' ['+jid+'-'+str(job['attempts']+1)+']'+base.suffix) if x))
        args=remotes.stream_args(job['remote'],output,size)
        sink=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
        with desk.lock:desk.processes[jid]=sink
        reader=threading.Thread(target=lines,args=(sink.stderr,),daemon=True);reader.start();readers.append(reader)
        if ffargs:
            producer=subprocess.Popen(ffargs,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            reader=threading.Thread(target=lines,args=(producer.stderr,),daemon=True);reader.start();readers.append(reader);response=producer.stdout
        total=0;started=time.monotonic();last=started
        # Blocking network/pipe I/O is watched independently, including pause and service shutdown.
        done=threading.Event()
        def monitor():
            while not done.wait(.5):
                if stopped():
                    terminate(producer);terminate(sink)
                    try:response.close()
                    except Exception:pass
                    return
        watcher=threading.Thread(target=monitor,daemon=True);watcher.start()
        try:
            while block:=response.read(64*1024):
                if stopped():raise InterruptedError('Remote transfer stopped; restart begins from zero')
                sink.stdin.write(block);total+=len(block);desk.record_usage(jid,len(block));desk.record_direction('rclone:'+job['remote'],0,len(block))
                elapsed=max(.01,time.monotonic()-started);speed=total/elapsed
                desk.live[jid]={'speed':speed,'eta':max(0,(size-total)/speed) if size and speed else None,'progress':min(99,100*total/size) if size else 0,'detail':'Streaming to '+job['remote']+':'+output,'activity':time.monotonic()}
                limit=settings.get('media_kib',0)*1024
                if limit and total/limit>elapsed:time.sleep(min(1,total/limit-elapsed))
            if producer and producer.wait()!=0:raise ValueError('Source stream failed: '+''.join(errors)[-1600:])
            if size is not None and total!=size:raise ValueError('Source ended before its declared size')
            sink.stdin.close()
            if sink.wait()!=0:raise ValueError('Remote upload failed: '+''.join(errors)[-1600:])
            if stopped():raise InterruptedError('Remote transfer stopped')
            completed=True
            desk.update_job(jid,status='done',progress=100,detail='Saved to '+job['remote']+':'+output,name=title)
        finally:done.set();watcher.join(timeout=2)
    finally:
        terminate(producer);terminate(sink)
        if response:
            try:response.close()
            except Exception:pass
        for reader in readers:reader.join(timeout=2)
        for proc in (producer,sink):
            if proc:
                for pipe in (proc.stdin,proc.stdout,proc.stderr):
                    if pipe:
                        try:pipe.close()
                        except OSError:pass
        if output and not completed:
            try:remotes.run('deletefile',remotes.target(job['remote'],output),json_output=False)
            except Exception:pass  # Reported as failed; only this job's unique object may be removed.
        try:session.save()
        except OSError:pass


def run(desk,job):
    # One cloud pipeline at a time on the 2 GB host. Waiting workers consume no
    # rclone chunk buffers or FFmpeg processes and remain cancellable.
    with desk.lock:
        if not hasattr(desk,'remote_transfer_lock'):desk.remote_transfer_lock=threading.Lock()
    while not desk.remote_transfer_lock.acquire(timeout=1):
        if desk.stop.is_set() or desk.job(job['id'])['status']!='running':return
        desk.update_job(job['id'],detail='Waiting for the cloud streaming slot')
    try:
        if desk.stop.is_set() or desk.job(job['id'])['status']!='running':return
        _run(desk,job)
    finally:desk.remote_transfer_lock.release()
