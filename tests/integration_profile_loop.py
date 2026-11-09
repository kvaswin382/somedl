"""Real gallery-dl GraphQL pagination stops a repeated page after worker restarts."""
import base64,contextlib,http.server,io,json,sys,tempfile,threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gallery_dl import extractor
from gallery_dl.extractor.instagram import InstagramPostsExtractor
from instagram_profile import run
from server import Desk
from media import build_command
import platforms
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9xkAAAAASUVORK5CYII=')
class Handler(http.server.BaseHTTPRequestHandler):
    requests=[]
    def log_message(self,*args):pass
    def do_GET(self):
        self.requests.append(self.path);self.send_response(200);self.send_header('Content-Length',str(len(PNG)));self.end_headers();self.wfile.write(PNG)
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
visited=[]
class LoopFixture(InstagramPostsExtractor):
    pattern=r'https://www\.instagram\.com/loopfixture/(posts|photos)/'
    def login(self):pass
    def _init(self):
        super()._init();self.api._prepare_graphql=lambda *args:({}, {'fb_api_req_friendly_name':'FixtureQuery','variables':json.dumps(args[1]),'doc_id':'1'})
    def request(self,url,**kwargs):
        if '/graphql' not in url:return super().request(url,**kwargs)
        cursor=json.loads(kwargs['data']['variables']).get('after');visited.append(cursor)
        n=2 if cursor=='A' else 1
        post={'pk':str(n),'code':'code'+str(n),'caption':{'text':'Fixture '+str(n)},'user':{'pk':'123','username':'loopfixture'},'taken_at':1700000000,'image_versions2':{'candidates':[{'url':f'http://127.0.0.1:{server.server_port}/{n}.png','width':1080,'height':1080}]}}
        return SimpleNamespace(text=json.dumps({'data':{'feed':{'edges':[{'node':post}],'page_info':{'has_next_page':True,'end_cursor':{None:'A','A':'B','B':'C'}.get(cursor,'D')}}}}))
    def posts(self):yield from self.api._pagination_graphql('FixtureQuery',{'after':None},'feed')
try:
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);(root/'ssd/Zedair').mkdir(parents=True)
        desk=Desk({'state':str(root/'state'),'mount':str(root/'ssd'),'require_mount':False});desk.set_setting('reserve_gib',0)
        platforms.save_cookies(desk.state,'instagram',{'fields':{'sessionid':'fixture'}})
        jid=desk.add_profile({'platform':'instagram','profile':'loopfixture','mode':'images'})['id'];stage=desk.staging(jid)
        args=build_command(desk.job(jid),stage,None,desk.settings(),desk.state);spec=json.loads(Path(args[2]).read_text())
        spec['config']['extractor']['instagram'].update({'sleep-request':0,'sleep-download':0,'sleep-retries':0,'retries':0})
        original=extractor.find;capture=io.StringIO()
        with patch.object(extractor,'find',side_effect=lambda url:LoopFixture.from_url(url) or original(url)),contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):
            statuses=[run(spec,slice_seconds=0) for _ in range(3)]
        assert statuses==[75,75,78],(statuses,capture.getvalue())
        assert visited==[None,'A','B'],visited
        assert Handler.requests==['/1.png','/2.png'],Handler.requests
        assert len(list(stage.glob('*.png')))==2
        assert 'pagination loop' in capture.getvalue()
        assert not json.loads((stage/'.profile-checkpoint-v1.json').read_text())['feeds']['posts']['done']
        desk.close()
    print('PASS: real GraphQL pagination stopped a repeated completed page after two worker restarts; two files fetched once each, third page refused before media requests, collection not marked complete.')
finally:server.shutdown();server.server_close()
