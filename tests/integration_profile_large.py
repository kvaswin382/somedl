"""300-post real gallery-dl image/carousel downloads, failure and page resume.
Only Instagram response data is replaced. All media requests use loopback.
"""
import base64
import contextlib
import http.server
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gallery_dl import extractor
from gallery_dl.extractor.instagram import InstagramPostsExtractor,InstagramAPI
from instagram_profile import run
from server import Desk
from media import build_command
import platforms

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9xkAAAAASUVORK5CYII=')
class Handler(http.server.BaseHTTPRequestHandler):
    requests=[];fail=True
    def log_message(self,*args):pass
    def do_GET(self):
        self.requests.append(self.path)
        if self.path=='/220.png' and Handler.fail:
            Handler.fail=False;self.send_response(500);self.end_headers();return
        self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(PNG)));self.end_headers();self.wfile.write(PNG)
local=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=local.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{local.server_port}'
visited=[]
def item(n,video=False):
    data={'pk':str(n),'image_versions2':{'candidates':[{'url':f'{base}/{n}.png','width':1080,'height':1080},{'url':base+'/low.png','width':100,'height':100}]}}
    if video:data['video_versions']=[{'url':base+'/video.mp4','width':1080,'height':1080}]
    return data
class Fixture(InstagramPostsExtractor):
    pattern=r'https://www\.instagram\.com/fixture/(posts|photos)/'
    def login(self):pass
    def posts(self):
        api=object.__new__(InstagramAPI);api.extractor=self
        def response(endpoint,params):
            start=int(params.get('max_id') or 0);visited.append(start)
            posts=[]
            for n in range(start,min(start+10,300)):
                post={**item(n*10),'pk':str(100000+n),'code':f'post{n}','caption':{'text':f'Fixture {n}'},'user':{'pk':'123','username':'fixture'},'taken_at':1700000000}
                if n%10==0:post['carousel_media']=[item(n*10),item(n*10+1,True),item(n*10+2)]
                posts.append(post)
            return {'items':posts,'more_available':start+10<300,'next_max_id':str(start+10)}
        api._call=response
        yield from api._pagination('/fixture')
try:
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);(root/'ssd/Zedair').mkdir(parents=True)
        desk=Desk({'state':str(root/'state'),'mount':str(root/'ssd'),'require_mount':False});desk.set_setting('reserve_gib',0)
        platforms.save_cookies(desk.state,'instagram',{'fields':{'sessionid':'fixture'}})
        jid=desk.add_profile({'platform':'instagram','profile':'fixture','images_only':True})['id'];stage=desk.staging(jid)
        args=build_command(desk.job(jid),stage,None,desk.settings(),desk.state);spec=json.loads(Path(args[2]).read_text())
        spec['config']['extractor']['instagram'].update({'sleep-request':0,'sleep-download':0,'sleep-retries':0,'retries':0})
        spec['config']['downloader']['http'].update({'retries':0,'sleep':0})
        original=extractor.find
        with patch.object(extractor,'find',side_effect=lambda url:Fixture.from_url(url) or original(url)),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            statuses=[]
            for attempt in range(40):
                status=run(spec,slice_seconds=0);statuses.append(status)
                if status==0:break
            assert status==0,(statuses,visited)
            before=len(Handler.requests);assert run(spec)==0;assert len(Handler.requests)==before
        files=list(stage.glob('*.png'))
        assert len(files)==330,len(files)
        assert all(p.read_bytes()==PNG for p in files)
        assert not any('/low.png' in u or '/video.mp4' in u for u in Handler.requests)
        assert statuses.count(1)==1,statuses
        assert visited[:4]==[0,10,20,20],visited
        assert set(visited)==set(range(0,300,10)),visited
        checkpoint=json.loads((stage/'.profile-checkpoint-v1.json').read_text());assert checkpoint['feeds']['posts']['done']
        desk.close()
    print('PASS: 300 posts across 30 pages, 330 real full-resolution image downloads, all carousel images retained, no video/thumbnail requests. Mid-page failure replayed page 20 without duplicate downloads; cooperative page restarts reached the end; completed retry made zero media requests.')
finally:local.shutdown();local.server_close()
