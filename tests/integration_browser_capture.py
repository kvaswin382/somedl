"""Actual sandboxed Chromium discovery against controlled local players.

Requires matching Playwright Chromium. Exit 77 means environment unavailable;
ZEDAIR_REQUIRE_BROWSER=1 converts that into a failure for CI. No browser security
flag is weakened. Loopback is allowed ONLY inside this fixture's patched validator.
"""
import http.cookiejar,http.server,json,os,sys,tempfile,threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
import browser_capture

try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,chromium_sandbox=True,timeout=15000)
        browser.close()
except Exception as exc:
    print('UNAVAILABLE: sandboxed Chromium launch: '+str(exc)[:2000])
    raise SystemExit(1 if os.getenv('ZEDAIR_REQUIRE_BROWSER')=='1' else 77)

class Handler(http.server.BaseHTTPRequestHandler):
    cookie_checks=[]
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.startswith('/cookie-check'):self.cookie_checks.append(self.path)
        base=f'http://127.0.0.1:{self.server.server_port}'
        mime='text/html';chunked=False
        if self.path=='/watch':body=b'<title>Nested player</title><iframe width="500" height="400" src="/embed"></iframe>'
        elif self.path=='/embed':body=b'''<button aria-label="Play video" onclick="start()">Play</button><video id="main" style="width:400px;height:240px"></video>
<script>async function start(){await fetch('/sources.json',{headers:{Authorization:'Bearer fixture-api'}});document.querySelector('video').src='/low.mp4';}</script>'''
        elif self.path=='/sources.json':
            mime='application/json';body=json.dumps({'sources':[{'file':'/low.mp4','height':360},{'file':'/high.mp4','height':2160}]}).encode()
        elif self.path=='/adaptive':body=b'''<title>Adaptive player</title><button aria-label="Play video" onclick="fetch('/master.m3u8');fetch('/variant.m3u8')">Play</button>'''
        elif self.path=='/authorized':body=b'''<button aria-label="Play video" onclick="fetch('/cookie-check?value='+encodeURIComponent(document.cookie));fetch('/auth.mp4',{headers:{Authorization:'Bearer fixture-media'}}).then(()=>document.querySelector('video').src='/auth.mp4')">Play</button><video style="width:400px;height:240px"></video>'''
        elif self.path=='/master.m3u8':mime='application/vnd.apple.mpegurl';chunked=True;body=b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1920x1080\nvariant.m3u8\n'
        elif self.path=='/variant.m3u8':mime='application/vnd.apple.mpegurl';chunked=True;body=b'#EXTM3U\n#EXTINF:4.0,\nsegment.ts\n#EXT-X-ENDLIST\n'
        else:mime='video/mp4';body=b'\0\0\0\x18ftypmp42'+b'fixture'
        self.send_response(200);self.send_header('Content-Type',mime)
        if self.path=='/authorized':self.send_header('Set-Cookie','refreshed=fixture; Path=/; HttpOnly')
        if not chunked:self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)

site=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=site.serve_forever,daemon=True);thread.start()
base=f'http://127.0.0.1:{site.server_port}'
original=browser_capture.validate_url

def validate(url):
    if url.startswith(base+'/'):return url
    return original(url)
try:
    with tempfile.TemporaryDirectory() as state,patch('browser_capture.validate_url',side_effect=validate),patch('download_http.validate_url',side_effect=validate),patch('browser_capture.available_memory',return_value=1024**3):
        for path,expected in (('/watch','/high.mp4'),('/adaptive','/master.m3u8'),('/authorized','/auth.mp4')):
            jar=http.cookiejar.MozillaCookieJar()
            if path=='/authorized':jar.set_cookie(http.cookiejar.Cookie(0,'private_session','fixture',None,False,'127.0.0.1',False,False,'/',True,False,None,True,None,None,{'HTTPOnly':''},False))
            found,title,jar=browser_capture.capture(base+path,jar,state,settings={'browser_timeout':25,'browser_settle':2})
            assert found['url']==base+expected,found
            if path=='/watch':assert 'authorization' not in found['headers'],found
            if path=='/authorized':
                assert found['headers']['authorization']=='Bearer fixture-media',found
                assert Handler.cookie_checks and all('private_session' not in s and 'refreshed' not in s for s in Handler.cookie_checks),Handler.cookie_checks
                jar.save(str(Path(state)/'cookies.txt'),ignore_discard=True)
                import platforms
                cookies={c.name:c for c in platforms.load_jar(Path(state)/'cookies.txt')}
                assert cookies['refreshed'].has_nonstandard_attr('HTTPOnly') and cookies['private_session'].has_nonstandard_attr('HTTPOnly')
    print('PASS: actual sandboxed Chromium clicked nested player, matched JSON variants to playback, selected higher quality, and distinguished a lengthless HLS master from its playback variant.')
finally:site.shutdown();site.server_close();thread.join()
