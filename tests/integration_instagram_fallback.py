"""Real empty-extractor handoff, HTTP transfer and lossless audio/video merge.
Discovery is fixture-controlled; Chromium discovery has a separate integration.
"""
import base64
import contextlib
import fcntl
import http.server
import io
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import instagram_fallback as fallback

with tempfile.TemporaryDirectory() as root:
    root=Path(root);dest=root/'stage';dest.mkdir();assets=root/'assets';assets.mkdir()
    subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=1920x1080:d=0.25','-an','-c:v','libx264','-threads','1','-preset','ultrafast',str(assets/'video.mp4')],check=True)
    subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=0.25','-vn','-c:a','aac','-threads','1',str(assets/'audio.m4a')],check=True)
    (assets/'photo.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jM1sAAAAASUVORK5CYII='))
    requests=[]
    def assert_locked():
        with (root/'browser-download.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            raise AssertionError('Browser download lease was released before transfer completed')
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            assert_locked();requests.append(self.path)
            path=assets/self.path.lstrip('/')
            data=path.read_bytes();self.send_response(200)
            self.send_header('Content-Type',{'.mp4':'video/mp4','.m4a':'audio/mp4','.png':'image/png'}[path.suffix])
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def discover(spec,jar):
        assert_locked()
        return [{'key':'Exact_01','url':base+'/video.mp4','audio_url':base+'/audio.m4a','video':True,'owner':'alice'},
                {'key':'Exact_02','url':base+'/photo.png','video':False,'owner':'alice'}]
    def validate(url):
        if not url.startswith(base+'/'):raise ValueError('Fixture forbids external requests')
        return url
    spec={'args':[sys.executable,'-c','pass'],'state':str(root),'dest':str(dest),'url':'https://www.instagram.com/p/Exact/','container':'mp4','quality':'best','settings':{}}
    output=io.StringIO()
    try:
        with patch('instagram_browser.discover',side_effect=discover),patch('download_http.validate_url',side_effect=validate),contextlib.redirect_stdout(output):assert fallback.run(spec)==0
        streams=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(dest/'Exact_01.mp4')]))['streams']
        video=next(s for s in streams if s['codec_type']=='video')
        assert video['height']==1080 and video['codec_name']=='h264',video
        assert any(s['codec_type']=='audio' and s['codec_name']=='aac' for s in streams),streams
        assert (dest/'Exact_02.png').read_bytes()==(assets/'photo.png').read_bytes()
        total=sum(int(line.split()[1]) for line in output.getvalue().splitlines() if line.startswith('ZEDBYTES '))
        assert total==sum(p.stat().st_size for p in assets.iterdir()),total
        assert all(requests.count('/'+name)==2 for name in ('video.mp4','audio.m4a','photo.png')),requests
        with (root/'browser-download.lock').open('a') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:server.shutdown();server.server_close();thread.join()
print('PASS: empty primary triggered fallback; actual HTTP transfers held one shared lease; ffmpeg preserved 1080p H.264 and AAC, image bytes/extension and received-byte totals matched.')
