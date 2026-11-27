"""Real gallery-dl child jobs, transfer, owner metadata and resumable archive.
Only the Instagram extractor responses are replaced with local fixtures.
"""
import contextlib,functools,http.server,io,sys,tempfile,threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gallery_dl import extractor
from gallery_dl.extractor.common import Extractor,Message
import instagram_story

with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);source=root/'source';source.mkdir();dest=root/'dest';dest.mkdir()
    photo=b'\xff\xd8\xff\xe0story fixture';(source/'photo.jpg').write_bytes(photo)
    class Handler(http.server.SimpleHTTPRequestHandler):
        count=0
        def log_message(self,*a):pass
        def do_GET(self):type(self).count+=1;super().do_GET()
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(source)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    class Story(Extractor):
        category='instagram';subcategory='stories';directory_fmt=();filename_fmt='{post_id}_{num}.{extension}';archive_fmt='{media_id}'
        pattern=r'https://www\.instagram\.com/stories/highlights/(\d+)/'
        def config(self,key,default=None):return 0 if key.startswith('sleep') else super().config(key,default)
        def items(self):
            data={'user':{'username':'fixture'},'post_id':self.match[1],'media_id':self.match[1],'num':1,'extension':'jpg'}
            yield Message.Directory,'',data
            yield Message.Url,f'http://127.0.0.1:{server.server_port}/photo.jpg',data
    class Highlights(Extractor):
        category='instagram';subcategory='highlights';pattern=r'https://www\.instagram\.com/fixture/highlights/'
        def items(self):
            for ident in (100,200):yield Message.Queue,f'https://www.instagram.com/stories/highlights/{ident}/',{'_extractor':Story}
    original=extractor.find
    def find(url):return Highlights.from_url(url) or Story.from_url(url) or original(url)
    cookie=root/'cookies';cookie.write_text('# Netscape HTTP Cookie File\n')
    spec={'url':'https://www.instagram.com/fixture/highlights/','dest':str(dest),'cookie':str(cookie),'settings':{}}
    try:
        output=io.StringIO()
        with patch('gallery_dl.extractor.find',side_effect=find),contextlib.redirect_stdout(output):
            assert instagram_story.run(spec)==0
            assert instagram_story.run(spec)==0
        metered=sum(int(line.split()[1]) for line in output.getvalue().splitlines() if line.startswith('ZEDBYTES '))
        assert metered==2*len(photo),(metered,output.getvalue())
        assert Handler.count==2,Handler.count
        assert len(list(dest.glob('*.jpg')))==2
        # Retry a failed publication from a populated gallery archive/workspace.
        from server import Desk
        (root/'ssd/Zedair').mkdir(parents=True)
        desk=Desk({'state':str(root/'state'),'mount':str(root/'ssd'),'require_mount':False})
        desk.set_setting('reserve_gib',0)
        try:
            jid=desk.add_jobs({'urls':['https://www.instagram.com/p/fixture/']})['ids'][0]
            stage=desk.staging(jid);retry_spec={**spec,'dest':str(stage)}
            with patch('gallery_dl.extractor.find',side_effect=find),contextlib.redirect_stdout(io.StringIO()):
                assert instagram_story.run(retry_spec)==0
                before=Handler.count
                desk.update_job(jid,status='error',phase='publish',attempts=2)
                desk.job_action(jid,'retry');desk.prepare_fresh_retry(desk.job(jid))
                assert instagram_story.run(retry_spec)==0
                assert Handler.count==before+2, 'Fresh retry must redownload rather than use the old gallery archive'
            assert desk.job(jid)['attempts']==2
        finally:desk.close()

        assert all(p.read_bytes()==photo for p in dest.glob('*.jpg'))
        assert output.getvalue().count('ZEDITEM story')==2,output.getvalue()
        assert 'ZEDOWNER fixture' in output.getvalue(),output.getvalue()
        print('PASS: real gallery-dl followed two highlight child collections, saved exact image bytes, emitted owner and completion metadata, and skipped completed IDs on retry. Instagram responses were fixtures.')
    finally:server.shutdown();server.server_close();thread.join()
