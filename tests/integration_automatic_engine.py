"""Real HTTP + actual orchestration: nested players, image typing and cookie handoff.
Unsupported site extractors and browser discovery are substituted in two explicit
places. This tests transfer and extraction orchestration, not Chromium execution.
"""
import contextlib,functools,http.cookiejar,http.server,io,subprocess,sys,tempfile,threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import download_engine
import download_http
from media import build_command
from server import DEFAULTS
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);source=root/'source';source.mkdir();state=root/'state';state.mkdir()
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=64x64:d=1','-c:v','mpeg4',str(source/'high.mp4')],check=True)
    (source/'watch').write_text('<title>Fixture title</title><iframe src="/embed"></iframe>')
    (source/'embed').write_text('<video><source src="/low.mp4" label="720p"><source src="/high.mp4" label="2160p"></video>')
    (source/'master.m3u8').write_text('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=64x64\nvariant.m3u8\n')
    class Handler(http.server.SimpleHTTPRequestHandler):
        seen=[]
        def log_message(self,*a):pass
        def guess_type(self,path):return 'text/html' if Path(path).name in ('watch','embed') else super().guess_type(path)
        def do_GET(self):
            self.seen.append((self.path,self.headers.get('Referer'),self.headers.get('Cookie')))
            if self.path.startswith('/protected'):
                if self.headers.get('Cookie')!='session=fixture' or not self.headers.get('Referer','').endswith('/watch'):
                    self.send_error(403);return
                self.path='/high.mp4'
            super().do_GET()
    site=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(source)))
    thread=threading.Thread(target=site.serve_forever,daemon=True);thread.start()
    try:
        base=f'http://127.0.0.1:{site.server_port}'
        for force in (False,True):
            dest=root/('rendered' if force else 'static');dest.mkdir()
            job={'id':'fixture','url':base+'/watch','kind':'video','quality':'best','container':'auto','playlist':0,'subtitles':0}
            spec={**job,'args':build_command(job,dest,None,DEFAULTS,state),'dest':str(dest),'state':str(state),'settings':{'refresh_attempts':0},'enabled':True,'force':force}
            def capture(url,jar,*args):
                jar.set_cookie(http.cookiejar.Cookie(0,'session','fixture',None,False,'127.0.0.1',False,False,'/',True,False,None,True,None,None,{},False))
                return {'url':base+'/protected','kind':'video','headers':{'referer':base+'/watch'}},'Browser title',jar
            output=io.StringIO()
            with patch('download_http.validate_url'),patch('page_media.validate_url'),patch('download_engine.execute',return_value=(1,'ERROR Unsupported URL')),patch('download_engine.gallery_args',return_value=None),patch('browser_capture.capture',side_effect=capture) as rendered,contextlib.redirect_stdout(output):
                assert download_engine.run(spec)==0,output.getvalue()
                assert rendered.call_count==int(force)
            result=next(dest.glob('*.mp4'));assert result.read_bytes()==(source/'high.mp4').read_bytes()
            assert result.stem==('Browser title' if force else 'Fixture title'),result
        assert not any(path=='/low.mp4' for path,ref,cookie in Handler.seen)
        assert any(path=='/protected' and cookie=='session=fixture' for path,ref,cookie in Handler.seen)
        import browser_capture,time
        candidates=[{'url':base+'/master.m3u8','kind':'manifest','headers':{}}]
        with patch('download_http.validate_url'):browser_capture.probe_manifests(candidates,http.cookiejar.MozillaCookieJar(),time.monotonic()+5)
        assert candidates[0]['master']
        print('PASS: new automatic pipeline selected highest nested player, saved exact bytes and titles, kept browser cookie and Referer for a protected CDN, and inspected HLS master within a bounded read. Browser discovery was substituted.')
    finally:site.shutdown();site.server_close();thread.join()
