"""Test detected URL/title/header/cookie handoff with real yt-dlp, local media only.
Browser discovery is substituted; this does not validate a live Chromium player.
"""
import contextlib,functools,http.cookiejar,http.server,io,json,os,subprocess,sys,tempfile,threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import browser_video
from media import build_command
from server import DEFAULTS
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);source=root/'source';source.mkdir();dest=root/'stage';dest.mkdir()
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=64x64:d=1','-c:v','mpeg4',str(source/'fixture.mp4')],check=True)
    class Handler(http.server.SimpleHTTPRequestHandler):
        referers=[]
        def log_message(self,*args):pass
        def do_GET(self):self.referers.append(self.headers.get('Referer'));super().do_GET()
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(source)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        job={'id':'fixture','url':'https://example.com/watch/123','kind':'video','quality':'best','playlist':0,'subtitles':0}
        args=build_command(job,dest,None,DEFAULTS,root);original=browser_video.execute;calls=[]
        def execute(command):
            calls.append(command)
            return (1,'ERROR Unsupported URL') if len(calls)==1 else original(command)
        found={'url':f'http://127.0.0.1:{server.server_port}/fixture.mp4','kind':'video','headers':{'referer':job['url'],'user-agent':'fixture'}}
        capture=io.StringIO()
        with patch('browser_video.discover',side_effect=ValueError('Fixture needs browser')),patch('browser_video.execute',side_effect=execute),patch('browser_video.detect',return_value=(found,'Website title',http.cookiejar.MozillaCookieJar())),patch('browser_video.profiles.browser_ready',return_value=True),patch('browser_video.validate_url'),contextlib.redirect_stdout(capture):
            status=browser_video.run({'args':args,'url':job['url'],'dest':str(dest),'cookie':'','enabled':True})
        assert status==0,capture.getvalue()
        files=list(dest.glob('*.mp4'));assert len(files)==1 and files[0].read_bytes()==(source/'fixture.mp4').read_bytes()
        info=json.loads(next(dest.glob('*.info.json')).read_text());assert info['title']=='Website title',info['title']
        assert all(r==job['url'] for r in Handler.referers),Handler.referers
        assert not (dest/'.browser-cookies.txt').exists()
        print('PASS: fallback handoff preserved website title, Referer and exact MP4 bytes using real yt-dlp; temporary cookies removed. Detection was substituted, no live site/browser tested.')
    finally:server.shutdown();server.server_close()
