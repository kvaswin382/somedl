"""Offline integration: real yt-dlp binary downloads a generated MP4 over loopback."""
import functools
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from media import build_command
from server import DEFAULTS

with tempfile.TemporaryDirectory() as temp:
    root=Path(temp); source=root/'source'; source.mkdir(); dest=root/'output'; dest.mkdir()
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=64x64:d=1','-c:v','mpeg4',str(source/'fixture.mp4')],check=True)
    handler=functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(source))
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    job={'id':'fixture','url':f'http://127.0.0.1:{server.server_port}/fixture.mp4','kind':'video','quality':'best','playlist':0,'subtitles':0}
    command=build_command(job,dest,None,DEFAULTS,root)
    result=subprocess.run(command,capture_output=True,text=True,timeout=60,env=dict(os.environ,TMPDIR=str(dest)))
    server.shutdown();server.server_close()
    assert result.returncode==0, result.stdout+result.stderr
    files=list(dest.glob('*.mp4'))
    assert len(files)==1, [p.name for p in dest.iterdir()]
    assert files[0].read_bytes()==(source/'fixture.mp4').read_bytes()
    assert 'ZEDTITLE ' in result.stdout
    assert 'ZEDPROG ' in result.stdout
    for line in result.stdout.splitlines():
        if line.startswith('ZEDPROG '):json.loads(line[8:])
    print('PASS: real yt-dlp completed exact-byte media transfer, title and JSON progress output.')
