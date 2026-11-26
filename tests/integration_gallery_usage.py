"""Gallery's embedded yt-dlp path: resumed payload excludes bytes already on disk."""
import contextlib,http.server,io,sys,tempfile,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gallery_meter import install
from yt_dlp import YoutubeDL
payload=b'fixture-video-data-'*4096
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        start=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0]);data=payload[start:]
        self.send_response(206 if start else 200);self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(len(data)))
        if start:self.send_header('Content-Range',f'bytes {start}-{len(payload)-1}/{len(payload)}')
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
with tempfile.TemporaryDirectory() as tmp:
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    dest=Path(tmp)/'saved.mp4';partial=Path(str(dest)+'.part');partial.write_bytes(payload[:1024]);output=io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            install()
            for _ in range(2):
                with YoutubeDL({'quiet':True,'no_warnings':True,'continuedl':True,'outtmpl':str(dest)}) as downloader:downloader.download([f'http://127.0.0.1:{server.server_port}/video.mp4'])
        actual=sum(int(line.split()[1]) for line in output.getvalue().splitlines() if line.startswith('ZEDBYTES '))
        assert actual==len(payload)-1024,(actual,len(payload),output.getvalue())
        assert dest.read_bytes()==payload
        print('PASS: native yt-dlp gallery transfer counts only resumed wire bytes and skips an existing output without double-counting.')
    finally:server.shutdown();server.server_close();thread.join()
