"""Real HTTP, yt-dlp and FFmpeg: HLS -> MP4, GIF -> MP4, JPEG unchanged."""
import contextlib,functools,http.server,io,json,subprocess,sys,tempfile,threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import download_engine
from media import build_command
from server import DEFAULTS

def ffmpeg(*args):subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-threads','1',*args],check=True)
def probe(path):return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);source=root/'source';source.mkdir()
    ffmpeg('-f','lavfi','-i','testsrc2=size=96x64:rate=10:duration=2','-f','lavfi','-i','sine=frequency=440:duration=2',
        '-c:v','libx264','-threads','1','-g','10','-c:a','aac','-hls_time','1','-hls_list_size','0',str(source/'stream.m3u8'))
    ffmpeg('-f','lavfi','-i','testsrc=size=65x63:rate=5:duration=1','-threads','1',str(source/'animation.gif'))
    ffmpeg('-f','lavfi','-i','color=c=blue:s=64x64:d=1','-frames:v','1','-threads','1',str(source/'photo.jpg'))
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*a):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(source)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        for name in ('stream.m3u8','animation.gif','photo.jpg'):
            dest=root/name;dest.mkdir()
            url=f'http://127.0.0.1:{server.server_port}/'+name
            job={'id':'fixture','url':url,'kind':'video','quality':'best','container':'mp4','playlist':0,'subtitles':0}
            spec={'url':url,'args':build_command(job,dest,None,DEFAULTS,root),'dest':str(dest),'state':str(root),
                  'settings':{**DEFAULTS,'refresh_attempts':0},'container':'mp4','quality':'best','enabled':False}
            output=io.StringIO()
            with patch('download_http.validate_url'),patch('download_engine.validate_url'),contextlib.redirect_stdout(output):
                status=download_engine.run(spec)
            assert status==0,output.getvalue()
            if name=='photo.jpg':
                files=list(dest.glob('*.jpg'));assert len(files)==1
                assert files[0].read_bytes()==(source/name).read_bytes();assert not list(dest.glob('*.mp4'))
            else:
                files=list(dest.glob('*.mp4'));assert len(files)==1,(list(dest.iterdir()),output.getvalue())
                data=probe(files[0]);video=next(s for s in data['streams'] if s['codec_type']=='video')
                assert video['codec_name']=='h264';assert float(data['format']['duration'])>=1
                if name.endswith('.m3u8'):
                    assert (video['width'],video['height'])==(96,64)
                    assert any(s['codec_type']=='audio' and s['codec_name']=='aac' for s in data['streams'])
                else:
                    assert video['width']==66 and video['height']==64 # Only pad odd GIF dimensions; never resize.
                    assert not list(dest.glob('*.gif'))
        print('PASS: real HLS remux kept H.264/AAC at source resolution, GIF encoded to MP4 with even padding, and MP4 format left JPEG bytes/extension unchanged.')
    finally:server.shutdown();server.server_close();thread.join()
