"""Real gallery-dl CLI regression, local media only: old print-only bug and recovery.

The fixture extractor substitutes Instagram pagination responses, while the actual
CLI parser, downloader, archives, postprocessors, worker and publisher execute.
No real website requests or account credentials are used.
"""
import base64
import functools
import http.server
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def worker():
    import gallery_dl
    from gallery_dl import extractor
    from gallery_dl.extractor.common import Extractor,Message
    from gallery_dl.extractor.instagram import InstagramAPI
    class FixtureInstagram(Extractor):
        category='instagram';subcategory='posts'
        directory_fmt=();filename_fmt='{post_shortcode}_{num:>02}.{extension}';archive_fmt='{media_id}'
        pattern=r'https://www\.instagram\.com/fixture/'
        def _init_cursor(self):return None
        def _update_cursor(self,value):return value
        def items(self):
            api=object.__new__(InstagramAPI);api.extractor=self
            def response(endpoint,params):
                page=int(params.get('max_id') or 0)
                return {'items':[{'post_shortcode':f'post{page}','media_id':f'{page}-{num}','num':num,'extension':'png','description':f'Photo {page}-{num}'} for num in (1,2)],'more_available':page<1,'next_max_id':'1'}
            api._call=response
            yield Message.Directory,"",{}
            for item in api._pagination('/fixture'):
                yield Message.Url,'http://127.0.0.1:'+os.environ['ZEDAIR_FIXTURE_PORT']+'/pixel.png',item
    original=extractor.find
    extractor.find=lambda url:FixtureInstagram.from_url(url) or original(url)
    if sys.argv[1]=='--profile':
        from instagram_profile import run
        spec=json.loads(Path(sys.argv[2]).read_text())
        spec['config']['extractor']['instagram'].update({'sleep-request':0,'sleep-download':0})
        raise SystemExit(run(spec))
    sys.argv=['gallery-dl',*sys.argv[2:]]
    raise SystemExit(gallery_dl.main())


def main():
    import platforms
    from media import build_command
    from server import Desk
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);source=root/'source';source.mkdir()
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9xkAAAAASUVORK5CYII=')
        (source/'pixel.png').write_bytes(png)
        class Handler(http.server.SimpleHTTPRequestHandler):
            requests=0
            def do_GET(self):Handler.requests+=1;super().do_GET()
            def log_message(self,*args):pass
        local_server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(source)))
        thread=threading.Thread(target=local_server.serve_forever,daemon=True);thread.start()
        os.environ['ZEDAIR_FIXTURE_PORT']=str(local_server.server_port)
        config={'state':str(root/'state'),'mount':str(root/'ssd'),'require_mount':False}
        (root/'ssd/Zedair').mkdir(parents=True)
        desk=Desk(config);desk.set_setting('reserve_gib',0)
        platforms.save_cookies(desk.state,'instagram',{'fields':{'sessionid':'fixture-not-a-login'}})
        jid=desk.add_profile({'platform':'instagram','profile':'fixture'})['id']
        job=desk.job(jid);stage=desk.staging(jid)
        def child(args):return [sys.executable,str(Path(__file__).resolve()),'--worker',*args[3:]]
        # Reproduce the shipped v1.3 command, including the stale archive it leaves.
        args=build_command(job,stage,None,desk.settings(),desk.state)
        spec=json.loads(Path(args[2]).read_text());conf=spec['config']
        conf['extractor']['archive']=str(stage/'.profile-archive.sqlite3')
        conf['extractor']['instagram'].update({'sleep-request':0,'sleep-download':0})
        confpath=desk.state/'old-config.json';confpath.write_text(json.dumps(conf))
        args=[sys.executable,'-m','gallery_dl','--config-ignore','--config',str(confpath),'--print','after:ZEDITEM {post_shortcode}','--',job['url']]
        old=subprocess.run(child(args),text=True,capture_output=True,timeout=30)
        assert old.returncode==0,old.stdout+old.stderr
        assert old.stdout.count('ZEDITEM ')==4,(old.stdout,old.stderr,args)
        assert not list(stage.glob('*.png')) and Handler.requests==0
        with sqlite3.connect(stage/'.profile-archive.sqlite3') as db:assert db.execute('SELECT count(*) FROM archive').fetchone()[0]==4
        desk.update_job(jid,items=4,phase='publish',status='error')
        # Simulate the pre-fix installation then its first v1.3.1 startup.
        desk.write("DELETE FROM settings WHERE key='instagram_download_fix_v131'")
        desk.close();desk=Desk(config)
        assert desk.job(jid)['items']==0 and desk.job(jid)['phase']=='download'
        desk.job_action(jid,'retry')
        def actual_command(*args):
            command=build_command(*args)
            return [sys.executable,str(Path(__file__).resolve()),'--profile',command[2]]
        with patch('server.validate_url'),patch('server.build_command',side_effect=actual_command):desk.run_job(desk.job(jid))
        final=desk.job(jid);assert final['status']=='done',final
        output=desk.root/'Instagram/fixture';files=list(output.glob('*.png'))
        assert len(files)==4 and all(p.read_bytes()==png for p in files)
        assert final['items']==4 and Handler.requests==4,final
        with sqlite3.connect(stage/'.profile-downloads-v2.sqlite3') as db:assert db.execute('SELECT count(*) FROM archive').fetchone()[0]==4
        # Restart the same job's download phase: real archive skips completed IDs.
        desk.update_job(jid,phase='download');desk.job_action(jid,'retry')
        with patch('server.validate_url'),patch('server.build_command',side_effect=actual_command):desk.run_job(desk.job(jid))
        assert desk.job(jid)['status']=='done' and desk.job(jid)['items']==4
        assert len(list(output.glob('*.png')))==4 and Handler.requests==4
        assert len(desk.rows('SELECT * FROM file_dates'))==4
        desk.close();local_server.shutdown();local_server.server_close()
    print('PASS: reproduced 4 false downloads, zero media requests and poisoned archive with old CLI; upgraded same job fetched/published 4 exact-byte media files across 2 pages; retry fetched no duplicates and kept an accurate count.')

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1] in ('--worker','--profile'):worker()
    else:main()
