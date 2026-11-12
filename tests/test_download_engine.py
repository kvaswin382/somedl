"""Regression coverage for the automatic resolver and safe transfer path.

Only the fixture HTTP server is allowed to use loopback. Production URL validation
is patched in the individual fixture calls, never relaxed in application code.
"""
import contextlib
import hashlib
import http.cookiejar
import http.server
import io
import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from test_desk import Fixture
from server import APIError
import browser_capture
import download_engine
import download_http as transfer
import engine_settings
import instagram_story
import maintenance
import platforms
from publishing import publish
from web_session import Session

JPEG=b'\xff\xd8\xff\xe0'+b'photograph'*2000
NEW=b'\x89PNG\r\n\x1a\n'+b'new image'*2000

class Site(http.server.BaseHTTPRequestHandler):
    seen=[]
    def log_message(self,*a):pass
    def do_GET(self):
        type(self).seen.append((self.path,dict(self.headers)))
        if self.path=='/error-page':body=b'<!doctype html><title>Login required</title>';mime='text/html'
        elif self.path=='/document':body=b'%PDF-1.7\nfixture';mime='application/pdf'
        else:body=NEW if self.path=='/changed' else JPEG;mime='application/octet-stream'
        etag='"new"' if self.path=='/changed' else '"same"'
        offset=int(self.headers.get('Range','bytes=0-')[6:-1])
        ranged='Range' in self.headers
        self.send_response(206 if ranged else 200)
        self.send_header('Content-Type',mime);self.send_header('ETag',etag)
        if ranged:self.send_header('Content-Range',f'bytes {offset}-{len(body)-1}/{len(body)}')
        body=body[offset:];self.send_header('Content-Length',str(len(body)));self.end_headers()
        self.wfile.write(body)

class Transfers(Fixture):
    @classmethod
    def setUpClass(cls):
        cls.site=http.server.ThreadingHTTPServer(('127.0.0.1',0),Site)
        cls.thread=threading.Thread(target=cls.site.serve_forever,daemon=True);cls.thread.start()
        cls.baseurl='http://127.0.0.1:'+str(cls.site.server_port)
    @classmethod
    def tearDownClass(cls):cls.site.shutdown();cls.site.server_close();cls.thread.join()
    def setUp(self):super().setUp();self.dest=self.base/'stage';self.dest.mkdir();Site.seen=[]
    def seed(self,etag='"same"'):
        key=hashlib.sha256(b'primary').hexdigest()[:20]
        part=self.dest/('.http-'+key+'.part');part.write_bytes(JPEG[:8000])
        transfer.atomic_json(self.dest/('.http-'+key+'.json'),{'etag':etag,'total':len(JPEG),'kind':'image','ext':'.jpg'})
        return part
    def get(self,path,name='Photo.mp4'):
        with patch('download_http.validate_url'),contextlib.redirect_stdout(io.StringIO()):
            return transfer.transfer(transfer.opener(),{'url':self.baseurl+path},self.dest,name)
    def test_magic_wins_over_url_and_requested_video_extension(self):
        out=self.get('/misleading.mp4')
        self.assertEqual(out.suffix,'.jpg');self.assertEqual(out.read_bytes(),JPEG)
        self.assertEqual(json.loads((self.dest/'.outputs.json').read_text()),[out.name])
    def test_resume_with_renewed_url_and_matching_entity(self):
        self.seed();out=self.get('/renewed-token')
        self.assertEqual(out.read_bytes(),JPEG)
        self.assertEqual(Site.seen[0][1]['Range'],'bytes=8000-')
        self.assertEqual(Site.seen[0][1]['If-Range'],'"same"')
    def test_changed_entity_never_splices_old_partial(self):
        self.seed();out=self.get('/changed')
        self.assertEqual(out.read_bytes(),NEW);self.assertEqual(out.suffix,'.png')
        self.assertEqual(len(Site.seen),2);self.assertNotIn('Range',Site.seen[1][1])
    def test_weak_validator_restarts(self):
        self.seed('W/"same"');out=self.get('/renewed-token')
        self.assertEqual(out.read_bytes(),JPEG);self.assertNotIn('Range',Site.seen[0][1])
    def test_error_html_is_not_published(self):
        part=self.seed('')
        with self.assertRaisesRegex(ValueError,'page or playlist'):self.get('/error-page')
        self.assertEqual(part.read_bytes(),JPEG[:8000]);self.assertFalse((self.dest/'.outputs.json').exists())
    def test_nonmedia_download_reaches_publication(self):
        self.get('/document','Document');jid=self.create_job();job=self.desk.job(jid)
        publish(self.desk,job,self.dest,self.desk.root)
        self.assertEqual((self.desk.root/'Document.pdf').read_bytes(),b'%PDF-1.7\nfixture')
    def test_mp4_setting_cannot_change_image_in_orchestrator(self):
        spec={'url':self.baseurl+'/image.jpg','dest':str(self.dest),'state':str(self.desk.state),'filename':'My image.jpg',
              'container':'mp4','quality':'best','args':[],'settings':{'refresh_attempts':0},'enabled':False}
        with patch('download_http.validate_url'),patch('download_engine.execute') as execute,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(download_engine.run(spec),0);execute.assert_not_called()
        self.assertEqual((self.dest/'My image.jpg').read_bytes(),JPEG)

class Selection(unittest.TestCase):
    def test_adaptive_master_beats_initial_low_mp4(self):
        low={'url':'https://cdn.test/low.mp4','kind':'video','height':360,'dom':True,'active':True}
        high={'url':'https://cdn.test/master.m3u8','kind':'manifest','master':True}
        self.assertEqual(browser_capture.select([low,high],{low['url']}),high)
    def test_variants_respect_per_job_ceiling(self):
        items=[{'url':f'https://cdn.test/{n}.mp4','kind':'video','height':n,'dom':True,'group':'player'} for n in (720,1080,2160)]
        self.assertEqual(browser_capture.select(items,set())['height'],2160)
        self.assertEqual(browser_capture.select(items,set(),'1080')['height'],1080)
    def test_unrelated_media_requires_unambiguous_player(self):
        items=[{'url':f'https://cdn.test/{n}.mp4','kind':'video'} for n in (1,2)]
        with self.assertRaisesRegex(ValueError,'unrelated'):browser_capture.select(items,set())
    def test_playing_low_variant_links_to_higher_json_variant(self):
        low={'url':'https://cdn.test/low.mp4','kind':'video','height':360,'dom':True,'groups':['player','json']}
        high={'url':'https://cdn.test/high.mp4','kind':'video','height':2160,'groups':['json']}
        self.assertEqual(browser_capture.select([low,high],{low['url']}),high)
    def test_segments_are_not_download_candidates(self):
        for path in ('a.ts','a.m4s','a.vtt'):self.assertEqual(browser_capture.kind('https://cdn.test/'+path,'video/mp2t'),'')
    def test_json_quality_is_kept(self):
        values=browser_capture.json_sources({'sources':[{'file':'https://cdn.test/v.mp4','height':2160}]},'player')
        self.assertEqual(values[0]['height'],2160)
    def test_relative_player_json_urls_resolve_against_response(self):
        values=browser_capture.json_sources({'file':'/high.mp4','height':2160},'https://cdn.test/config/player.json')
        self.assertEqual(values[0]['url'],'https://cdn.test/high.mp4')
    def test_authorization_is_not_a_global_ytdlp_header(self):
        args=download_engine.media_args({'args':['yt-dlp','-o','test.%(ext)s','--','https://source.test']},'https://cdn.test','cookies',{'authorization':'secret','referer':'https://source.test'})
        self.assertNotIn('authorization:secret',args);self.assertIn('referer:https://source.test',args)
    def test_redirection_strips_cross_origin_credentials(self):
        import urllib.request
        req=urllib.request.Request('https://origin.test/a',headers={'Authorization':'Bearer secret','Origin':'https://origin.test'})
        with patch('download_http.validate_url'):
            result=transfer.Redirect().redirect_request(req,None,302,'',{},'https://cdn.test/a')
        self.assertNotIn('Authorization',result.headers);self.assertNotIn('Origin',result.headers)
    def test_redirect_destinations_are_validated(self):
        import urllib.request
        with self.assertRaises(ValueError):
            transfer.Redirect().redirect_request(urllib.request.Request('https://example.com'),None,302,'',{},'http://127.0.0.1/private')
    def test_host_only_cookie_cannot_leak_to_subdomain(self):
        import urllib.request
        jar=http.cookiejar.CookieJar()
        jar.set_cookie(http.cookiejar.Cookie(0,'session','private',None,False,'source.example',False,False,'/',True,True,None,True,None,None,{},False))
        transfer.opener(jar)
        same=urllib.request.Request('https://source.example/file');other=urllib.request.Request('https://sub.source.example/file')
        jar.add_cookie_header(same);jar.add_cookie_header(other)
        self.assertEqual(same.get_header('Cookie'),'session=private');self.assertFalse(other.has_header('Cookie'))

class SettingsAndTools(Fixture):
    def test_story_targets(self):
        self.assertEqual(instagram_story.target('@Fixture','stories'),('fixture','https://www.instagram.com/stories/fixture/'))
        self.assertEqual(instagram_story.target('https://www.instagram.com/stories/fixture/123/','stories')[0],'fixture')
        self.assertEqual(instagram_story.target('fixture','highlights')[1],'https://www.instagram.com/fixture/highlights/')
        self.assertEqual(instagram_story.target('https://www.instagram.com/stories/highlights/123/','highlights'),('','https://www.instagram.com/stories/highlights/123/'))
        for bad in ('https://evil.test/fixture','../private','https://instagram.com:444/fixture'):
            with self.assertRaises(ValueError):instagram_story.target(bad,'stories')
    def test_story_job_separate_from_regular_downloader(self):
        platforms.save_cookies(self.desk.state,'instagram',{'fields':{'sessionid':'fixture'}})
        jid=self.desk.add_stories({'profile':'fixture','mode':'stories'})['id'];job=self.desk.job(jid)
        self.assertEqual(job['profile_mode'],'stories');self.assertEqual(job['subdir'],'Instagram/fixture')
        with self.assertRaises(APIError):self.desk.add_stories({'profile':'fixture','mode':'stories'})
    def test_story_tool_respects_platform_disabled(self):
        prefs=self.desk.platform_settings();prefs['instagram']['enabled']=False;self.desk.set_setting('platforms',prefs)
        with self.assertRaisesRegex(APIError,'Enable Instagram'):self.desk.add_stories({'profile':'fixture'})
    def test_telegram_token_is_masked_in_snapshot(self):
        self.desk.set_setting('telegram_bot_token','123:private')
        settings=self.desk.snapshot()['settings']
        self.assertEqual(settings['telegram_bot_token'],'');self.assertTrue(settings['telegram_token_set'])
    def test_claude_telegram_settings_migrate_without_losing_opt_in(self):
        self.desk.set_setting('telegram_bot_token','123:fixture');self.desk.set_setting('telegram_chat_id','-123')
        self.desk.write("DELETE FROM settings WHERE key='telegram_errors'")
        self.desk.close();self.desk=_desk_tests.Desk(self.config)
        self.assertTrue(self.desk.get_setting('telegram_errors'))
        self.desk.set_setting('telegram_errors',False);self.desk.close();self.desk=_desk_tests.Desk(self.config)
        self.assertFalse(self.desk.get_setting('telegram_errors'))
    def test_settings_bounds_and_strict_booleans(self):
        self.assertEqual(engine_settings.checked({'browser_timeout':'120'})['browser_timeout'],120)
        for data in ({'simultaneous_downloads':21},{'auto_cookies':'false'},{'telegram_chat_id':'a;command'},{'browser_timeout':True}):
            with self.assertRaises(ValueError):engine_settings.checked(data)
    def test_cookie_persistence_and_domain_scope(self):
        s=Session(self.desk.state,'https://a.test/watch')
        c=http.cookiejar.Cookie(0,'session','private',None,False,'a.test',False,False,'/',True,True,None,True,None,None,{},False)
        s.jar.set_cookie(c);s.save()
        self.assertEqual(next(iter(Session(self.desk.state,'https://a.test/other').jar)).value,'private')
        self.assertFalse(list(Session(self.desk.state,'https://b.test/other').jar))
        self.assertEqual(s.path.stat().st_mode & 0o777,0o600)
    def test_parallel_session_save_keeps_new_cookies(self):
        a=Session(self.desk.state,'https://a.test');b=Session(self.desk.state,'https://a.test')
        for session,name in ((a,'one'),(b,'two')):
            session.jar.set_cookie(http.cookiejar.Cookie(0,name,name,None,False,'a.test',False,False,'/',True,True,None,True,None,None,{},False));session.save()
        self.assertEqual({c.name for c in Session(self.desk.state,'https://a.test').jar},{'one','two'})
    def test_cookie_deletion_wins_over_old_worker(self):
        from web_session import forget
        session=Session(self.desk.state,'https://www.reddit.com')
        session.jar.set_cookie(http.cookiejar.Cookie(0,'reddit_session','private',None,False,'.reddit.com',True,True,'/',True,True,None,True,None,None,{},False))
        session.save();forget(self.desk.state,'reddit');session.save()
        self.assertFalse(list(Session(self.desk.state,'https://www.reddit.com').jar))
    def test_disabled_session_does_not_load_saved_cookies(self):
        session=Session(self.desk.state,'https://a.test')
        session.jar.set_cookie(http.cookiejar.Cookie(0,'session','private',None,False,'a.test',False,False,'/',True,True,None,True,None,None,{},False));session.save()
        self.assertFalse(list(Session(self.desk.state,'https://a.test',enabled=False).jar))
    def test_update_rejects_arbitrary_package_before_command(self):
        with patch('maintenance.subprocess.run') as run:
            for package in ('requests','yt-dlp;reboot','https://evil.test/pkg','--upgrade'):
                with self.assertRaises(ValueError):maintenance.request_update(self.desk,package)
            run.assert_not_called()
    def test_managed_update_keeps_no_new_privileges(self):
        with patch('maintenance.subprocess.run',return_value=subprocess.CompletedProcess([],0)) as run:
            maintenance.request_update(self.desk,'yt-dlp')
        self.assertEqual(run.call_args.args[0],['/usr/bin/systemctl','--no-ask-password','start','--no-block','zedair-update@yt-dlp.service'])
    def test_inventory_survives_inaccessible_optional_integration(self):
        blocked=MagicMock();blocked.__truediv__.return_value.read_text.side_effect=PermissionError('private')
        blocked.__truediv__.return_value.is_file.side_effect=PermissionError('private')
        with patch.object(maintenance,'UPDATE_STATE',blocked),patch.object(maintenance.shutil,'which',return_value=None):
            result=maintenance.inventory(self.desk)
        self.assertFalse(result['update_available']);self.assertTrue(result['packages'])
    def test_cleanup_keeps_resumable_partials_and_active_stage(self):
        jid=self.create_job();stage=self.desk.staging(jid);(stage/'movie.part').write_bytes(b'partial');(stage/'.browser-error.jpg').write_bytes(b'private')
        maintenance.clear_cache(self.desk,{})
        self.assertTrue((stage/'movie.part').exists());self.assertTrue((stage/'.browser-error.jpg').exists())
        self.desk.update_job(jid,status='error');maintenance.clear_cache(self.desk,{})
        self.assertTrue((stage/'movie.part').exists());self.assertFalse((stage/'.browser-error.jpg').exists())
    def test_usage_groups_by_submitted_domain(self):
        jid=self.create_job();self.desk.record_usage(jid,1200);self.desk.flush_usage(force=True)
        result=self.desk.usage_summary('day');self.assertIn('youtube.com',json.dumps(result));self.assertIn('1200',json.dumps(result))
    def test_cancel_one_concurrent_worker_preserves_the_other(self):
        ids=[self.create_job('https://example.com/video/'+str(n)) for n in range(2)]
        command=[sys.executable,'-c','import time;time.sleep(30)']
        with patch('server.validate_url'),patch('server.build_command',return_value=command):
            threads=[threading.Thread(target=self.desk.run_job,args=(self.desk.job(jid),)) for jid in ids]
            for thread in threads:thread.start()
            try:
                deadline=time.monotonic()+5
                while len(self.desk.processes)<2 and time.monotonic()<deadline:time.sleep(.02)
                self.assertEqual(len(self.desk.processes),2)
                second=self.desk.processes[ids[1]]
                self.desk.job_action(ids[0],'cancel');threads[0].join(5)
                self.assertIsNone(second.poll());self.assertEqual(self.desk.job(ids[1])['status'],'running')
            finally:
                self.desk.stop.set();self.desk.stop_process()
                for thread in threads:thread.join(8)


class ShareResolution(unittest.TestCase):
    def test_reddit_and_threads_mobile_redirects(self):
        import urllib.error
        for source,destination in (
            ('https://www.reddit.com/r/fixture/s/abc','https://www.reddit.com/r/fixture/comments/def/title/?utm_source=share'),
            ('https://www.threads.com/share/abc','https://www.threads.com/@fixture/post/ABC?xmt=tracking')):
            with patch('download_engine.validate_url'),patch('download_http.request',side_effect=urllib.error.HTTPError(source,302,'Found',{'Location':destination},None)) as request:
                result=download_engine.resolve_share(source,http.cookiejar.MozillaCookieJar())
            self.assertEqual(result,destination.split('?')[0]);self.assertEqual(request.call_count,1)
    def test_share_redirect_cannot_leave_platform(self):
        import urllib.error
        source='https://www.reddit.com/r/fixture/s/abc'
        with patch('download_engine.validate_url'),patch('download_http.request',side_effect=urllib.error.HTTPError(source,302,'Found',{'Location':'https://evil.test/page'},None)):
            with self.assertRaisesRegex(ValueError,'outside its platform'):download_engine.resolve_share(source,http.cookiejar.MozillaCookieJar())

import test_desk as _desk_tests
class EngineHTTP(Fixture):
    def setUp(self):
        super().setUp()
        self.server=_desk_tests.Server(('127.0.0.1',0),_desk_tests.Handler);self.server.desk=self.desk
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.cookie='';self.csrf=''
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()
    request=_desk_tests.HTTPTests.request
    login=_desk_tests.HTTPTests.login
    def test_new_endpoints_require_authentication_and_csrf(self):
        for path in ('/api/jobs/retry-all','/api/engines/update','/api/cache/clear','/api/instagram/stories','/api/telegram/test'):
            self.assertEqual(self.request(path,{},cookie=False)[0],401)
        for path in ('/api/usage?period=day','/api/engines','/api/favicon?id=any'):
            self.assertEqual(self.request(path,cookie=False)[0],401)
        self.login()
        self.assertEqual(self.request('/api/jobs/retry-all',{},csrf=False)[0],403)
    def test_retry_all_leaves_paused_and_finished_jobs_untouched(self):
        self.login();ids=[]
        for i,status in enumerate(('error','error','paused','done')):
            jid=self.create_job('https://example.com/'+str(i));self.desk.update_job(jid,status=status);ids.append(jid)
        self.assertEqual(self.request('/api/jobs/retry-all',{})[0],200)
        self.assertEqual([self.desk.job(j)['status'] for j in ids],['queued','queued','paused','done'])
    def test_masked_token_preserved_and_explicitly_cleared(self):
        self.login();self.desk.rpc.call=lambda *a,**k:{}
        self.assertEqual(self.request('/api/settings',{'telegram_bot_token':'123:secret','browser_timeout':120})[0],200)
        self.assertEqual(self.request('/api/settings',{'telegram_bot_token':''})[0],200)
        self.assertEqual(self.desk.get_setting('telegram_bot_token'),'123:secret')
        self.assertEqual(self.request('/api/settings',{'clear_telegram_token':True})[0],200)
        self.assertEqual(self.desk.get_setting('telegram_bot_token'),'')
        self.assertEqual(self.request('/api/settings',{'simultaneous_downloads':21})[0],400)

if __name__=='__main__':unittest.main()
