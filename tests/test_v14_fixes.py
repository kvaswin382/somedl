import io,json,socket,threading,time
from pathlib import Path
from unittest.mock import patch
from test_desk import Fixture
from server import APIError
from media import normalize_url,url_key,validate_url
from page_media import PlayerHTML,choose
from instagram_profile import PageHistory,observe_page,PaginationLoop
from fileops import manage_batch,inspect_folder,delete_folder
from browser_video import run

class FixTests(Fixture):
    def setUp(self):super().setUp();self.desk.rpc.call=lambda *a,**k:{'torrents':[]}
    def stamp(self,p):return f'{p.stat().st_size}:{p.stat().st_mtime_ns}'
    def test_defaults_and_duplicate_url_variants_all_history(self):
        first=self.create_job();j=self.desk.job(first);self.assertEqual((j['quality'],j['container']),('best','auto'))
        self.desk.update_job(first,status='done')
        result=self.desk.add_jobs({'urls':['https://youtu.be/fixture?si=share','https://youtube.com/watch?v=fixture&utm_source=x#t=9']})
        self.assertEqual(result,{'ids':[],'skipped':2})
        self.assertEqual(url_key('https://instagram.com/p/abc/?igsh=shared'),url_key('https://www.instagram.com/p/abc/'))
        self.assertNotEqual(url_key('https://example.com/a?id=one'),url_key('https://example.com/a?id=two'))
    def test_mobile_normalization_and_dns_diagnostics(self):
        self.assertEqual(normalize_url('https://m.hqporner.com/hdporn/123-fixture.html'),'https://hqporner.com/hdporn/123-fixture.html')
        with patch('media.socket.getaddrinfo',side_effect=socket.gaierror(socket.EAI_AGAIN,'temporary')) as dns,patch('media.time.sleep'):
            with self.assertRaisesRegex(ValueError,'DNS lookup failed for www.eporner.com'):validate_url('https://www.eporner.com/video-fixture/')
            self.assertEqual(dns.call_count,3)
    def test_static_player_sources_and_embeds_quality(self):
        page=PlayerHTML('https://example.com/watch/1')
        page.feed('<title>Example film</title><iframe data-src="//player.example.org/video/12"></iframe><video><source src="/low.mp4" label="720p"><source src="/high.mp4" label="1080p"></video>')
        self.assertEqual(page.embeds,['https://player.example.org/video/12'])
        self.assertEqual(choose(page.media,mp4=True)['url'],'https://example.com/high.mp4')
        self.assertEqual(choose(page.media,'720',True)['url'],'https://example.com/low.mp4')
        page.feed('<video src="/ad.mp4"></video>')
        with self.assertRaises(ValueError):choose(page.media)
    def test_rotating_cursors_and_repeated_pages_survive_restart(self):
        path=self.base/'pages.sqlite';state={'_history':PageHistory(path,'posts')}
        posts=[{'node':{'id':'one'}},{'node':{'id':'two'}}];observe_page(state,posts)
        state['_history'].commit('A',state['_page_identity']);state['_history']=PageHistory(path,'posts')
        self.assertTrue(state['_history'].seen('cursor','A'))
        with self.assertRaises(PaginationLoop):observe_page(state,list(reversed(posts)))
        observe_page(state,[{'id':'three'}]);observe_page(state,[{'id':'three'}]) # uncommitted failed page may replay
    def test_queue_removal_stops_worker_and_keeps_files(self):
        jid=self.create_job();file=self.desk.root/'keep.mp4';file.write_bytes(b'finished')
        command=[__import__('sys').executable,'-c','import time;time.sleep(20)']
        with patch('server.validate_url'),patch('server.build_command',return_value=command):
            t=threading.Thread(target=self.desk.run_job,args=(self.desk.job(jid),));t.start()
            for _ in range(100):
                if self.desk.process:break
                time.sleep(.02)
            self.desk.job_action(jid,'remove');self.assertFalse(self.desk.snapshot()['jobs']);t.join(8);self.assertFalse(t.is_alive())
        self.assertFalse(self.desk.rows('SELECT id FROM jobs'));self.assertEqual(file.read_bytes(),b'finished')
    def test_engine_marker_persisted(self):
        jid=self.create_job();self.desk.consume(io.BytesIO(b'ZEDENGINE browser\nZEDPROFILE Playing the embedded player\n'),jid)
        self.assertEqual(self.desk.job(jid)['engine'],'browser')
    def test_recursive_delete_requires_current_inventory_and_protects_torrents(self):
        folder=self.desk.destination('Folder/Sub',create=True);(folder/'a.mp4').write_bytes(b'keep')
        info=inspect_folder(self.desk,{'path':'Folder'});self.assertEqual(info['files'],1)
        (folder/'new.jpg').write_bytes(b'image')
        with self.assertRaisesRegex(ValueError,'changed'):delete_folder(self.desk,{'path':'Folder','stamp':info['stamp'],'confirm':True})
        info=inspect_folder(self.desk,{'path':'Folder'})
        self.desk.rpc.call=lambda *a,**k:{'torrents':[{'downloadDir':str(self.desk.root),'files':[{'name':'Folder/Sub/a.mp4'}]}]}
        with self.assertRaisesRegex(ValueError,'torrent'):delete_folder(self.desk,{'path':'Folder',**info,'confirm':True})
        self.desk.rpc.call=lambda *a,**k:{'torrents':[]}
        delete_folder(self.desk,{'path':'Folder',**info,'confirm':True});self.assertFalse(folder.exists())
        with self.assertRaises((ValueError,APIError)):inspect_folder(self.desk,{'path':''})
    def test_recursive_delete_rejects_symlink_without_touching_target(self):
        folder=self.desk.destination('Folder',create=True);outside=self.base/'private';outside.write_bytes(b'keep');(folder/'link').symlink_to(outside)
        with self.assertRaises(ValueError):inspect_folder(self.desk,{'path':'Folder'})
        self.assertEqual(outside.read_bytes(),b'keep')
    def test_batch_copy_and_move_preflight_collision(self):
        files=[]
        for name in ('one.jpg','two.mp4'):
            p=self.desk.root/name;p.write_bytes(name.encode());files.append({'path':name,'stamp':self.stamp(p)})
        target=self.desk.destination('Target',create=True);(target/'one.jpg').write_bytes(b'existing')
        with self.assertRaises(ValueError):manage_batch(self.desk,{'action':'move','files':[files[0],{**files[1],'stamp':'stale'}],'folder':'Target'})
        self.assertTrue((self.desk.root/'one.jpg').exists())
        result=manage_batch(self.desk,{'action':'copy','files':files,'folder':'Target'});self.assertEqual(result['remaining'],0)
        self.assertEqual((target/'one (1).jpg').read_bytes(),b'one.jpg');self.assertEqual((target/'one.jpg').read_bytes(),b'existing')
        self.desk.destination('Moved',create=True);result=manage_batch(self.desk,{'action':'move','files':files,'folder':'Moved'});self.assertEqual(len(result['results']),2);self.assertFalse((self.desk.root/'two.mp4').exists())
    def test_profile_images_radio_and_cookie_mode_dedup(self):
        import platforms
        platforms.save_cookies(self.desk.state,'instagram',{'fields':{'sessionid':'fixture'}})
        a=self.desk.add_profile({'platform':'instagram','profile':'fixture','mode':'everything'})
        b=self.desk.add_profile({'platform':'instagram','profile':'fixture','mode':'images'})
        self.assertNotEqual(a['id'],b['id']);self.assertEqual(self.desk.job(b['id'])['images_only'],1)
    def test_browser_forced_retry_skips_normal_extractor(self):
        with patch('browser_video.profiles.browser_ready',return_value=False),patch('browser_video.execute') as execute:
            with self.assertRaisesRegex(ValueError,'not installed'):run({'force':True,'args':[],'enabled':True})
            execute.assert_not_called()

    def test_folder_in_use_cannot_be_deleted(self):
        self.desk.destination('Active',create=True)
        self.desk.add_jobs({'urls':['https://example.com/file'],'folder':'Active'})
        info=inspect_folder(self.desk,{'path':'Active'})
        with self.assertRaisesRegex(ValueError,'queued download'):delete_folder(self.desk,{'path':'Active',**info,'confirm':True})
        self.assertTrue((self.desk.root/'Active').is_dir())
    def test_missing_browser_diagnostic_overrides_earlier_unsupported(self):
        import platforms
        kind,detail=platforms.failure_kind('ERROR Unsupported URL\nERROR Browser fallback: Browser engine is not installed')
        self.assertEqual(kind,'engine')
    def test_browser_request_cannot_change_a_running_job(self):
        jid=self.create_job();self.desk.update_job(jid,status='running')
        with self.assertRaises(APIError):self.desk.job_action(jid,'browser')
        self.assertEqual(self.desk.job(jid)['browser_force'],0)

    def test_completed_job_requires_explicit_removal_for_redownload(self):
        jid=self.create_job();self.desk.update_job(jid,status='done')
        with self.assertRaises(APIError):self.desk.job_action(jid,'retry')
        self.desk.job_action(jid,'remove');self.assertEqual(len(self.desk.add_jobs({'urls':['https://www.youtube.com/watch?v=fixture']})['ids']),1)
