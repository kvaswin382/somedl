"""Real HTTP nested-player discovery and real yt-dlp transfer; local fixtures only."""
import contextlib,functools,http.server,io,json,subprocess,sys,tempfile,threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import browser_video
from media import build_command
from server import DEFAULTS
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);source=root/'source';source.mkdir();dest=root/'stage';dest.mkdir()
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=64x64:d=1','-c:v','mpeg4',str(source/'high.mp4')],check=True)
    (source/'watch').write_text('<title>Fixture page title</title><iframe src="/embed"></iframe>')
    (source/'embed').write_text('<video><source src="/low.mp4" label="720p"><source src="/high.mp4" label="1080p"></video>')
    class Handler(http.server.SimpleHTTPRequestHandler):
        requests=[]
        def log_message(self,*a):pass
        def do_GET(self):self.requests.append((self.path,self.headers.get('Referer')));super().do_GET()
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(source)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        job={'id':'fixture','url':base+'/watch','kind':'video','quality':'best','container':'mp4','playlist':0,'subtitles':0}
        args=build_command(job,dest,None,DEFAULTS,root);original=browser_video.execute;calls=[]
        def execute(command):
            calls.append(command)
            return (1,'ERROR Unsupported URL') if len(calls)==1 else original(command)
        capture=io.StringIO()
        with patch('browser_video.execute',side_effect=execute),patch('browser_video.profiles.browser_ready',return_value=False),patch('browser_video.validate_url'),patch('page_media.validate_url'),contextlib.redirect_stdout(capture):
            status=browser_video.run({'args':args,'url':job['url'],'dest':str(dest),'cookie':'','enabled':True})
        assert status==0,capture.getvalue()
        assert 'ZEDENGINE page' in capture.getvalue() and 'ZEDENGINE browser' not in capture.getvalue()
        files=list(dest.glob('*.mp4'));assert len(files)==1 and files[0].read_bytes()==(source/'high.mp4').read_bytes()
        info=json.loads(next(dest.glob('*.info.json')).read_text());assert info['title']=='Fixture page title'
        assert ('/embed',base+'/watch') in Handler.requests
        assert ('/high.mp4',base+'/embed') in Handler.requests
        assert not any('/low.mp4'==path for path,ref in Handler.requests)
        assert not (dest/'.browser-cookies.txt').exists()
        print('PASS: nested iframe HTML discovery selected highest labelled MP4, preserved parent title and embed Referer, and real yt-dlp saved exact media bytes without Chromium.')
    finally:server.shutdown();server.server_close()
