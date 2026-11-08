import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from test_desk import Fixture
import test_revision as rev
import test_v12 as prior
import platforms
import profiles
from media import build_command,video_format
from systemstats import SystemStats,memory_stats,cpu_ticks
from facebook_profile import canonical_link,photo_source,media_host,browser_cookies
from server import APIError

spec=importlib.util.spec_from_file_location('local_setup',Path(__file__).resolve().parents[1]/'setup-local.py')
local_setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(local_setup)


class ProfileTests(Fixture):
    def setUp(self):
        super().setUp()
        platforms.save_cookies(self.desk.state,'instagram',{'fields':{'sessionid':'fixture-session'}})
        platforms.save_cookies(self.desk.state,'facebook',{'fields':{'c_user':'123','xs':'fixture-xs'}})

    def test_routing_facebook_reddit_and_manual_session(self):
        for url,platform,folder in [('https://fb.watch/abcd','facebook','Facebook'),('https://www.facebook.com/reel/123','facebook','Facebook'),('https://www.reddit.com/r/test/comments/abc/a/','reddit','Reddit'),('https://v.redd.it/abc','reddit','Reddit')]:
            job=self.desk.job(self.create_job(url));self.assertEqual(platforms.platform_for(url),platform);self.assertEqual(job['subdir'],folder)
            args=build_command(job,self.desk.root,None,self.desk.settings(),self.desk.state)
            self.assertIn('yt_dlp',args)
        self.assertEqual(platforms.platform_for('https://facebook.com.evil.test/'),'other')
        self.assertTrue(platforms.cookie_status(self.desk.state,'facebook')['usable'])
        self.assertNotIn('fixture-xs',json.dumps(self.desk.snapshot()))
        with self.assertRaises(ValueError):platforms.save_cookies(self.desk.state,'facebook',{'fields':{'c_user':'123'}})

    def test_mp4_setting_is_resolved_and_persisted_per_job(self):
        prefs=self.desk.platform_settings();prefs['youtube']['container']='mp4'
        self.desk.set_setting('platforms',prefs)
        jid=self.create_job();job=self.desk.job(jid)
        self.assertEqual(job['container'],'mp4')
        args=build_command(job,self.desk.root,None,self.desk.settings(),self.desk.state)
        self.assertEqual(args[args.index('-f')+1],'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]')
        self.assertIn('--merge-output-format',args);self.assertIn('--remux-video',args)
        self.assertNotIn('--recode-video',args)
        self.assertEqual(video_format('1080',True),'bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]')
        self.assertEqual(video_format('audio',True),'ba/b')
        with self.assertRaises(APIError):self.desk.add_jobs({'urls':['https://example.com/a'],'container':'invalid'})
        jid=self.desk.add_jobs({'urls':['https://youtube.com/watch?v=other'],'container':'auto'})['ids'][0]
        self.assertEqual(self.desk.job(jid)['container'],'auto')

    def test_profile_destinations_modes_and_resume_archive(self):
        for mode in profiles.MODES:
            result=self.desk.add_profile({'platform':'instagram','profile':'@Example.User','mode':mode})
            job=self.desk.job(result['id']);self.assertEqual(job['subdir'],'Instagram/example.user')
            dest=self.desk.staging(job['id']);args=build_command(job,dest,self.desk.cookie_for(job),self.desk.settings(),self.desk.state)
            config=json.loads((self.desk.state/('profile-'+job['id']+'.json')).read_text())['config']['extractor']
            self.assertIsNone(config['instagram']['max-posts'])
            self.assertEqual(config['instagram']['include'],{'posts':['photos'],'reels':['reels'],'everything':['posts','reels']}[mode])
            self.assertTrue(config['archive'].startswith(str(dest)))
            self.assertTrue(args[1].endswith('instagram_profile.py'))
        with self.assertRaises(APIError):self.desk.add_profile({'platform':'instagram','profile':'example.user','mode':'posts'})

    def test_individual_instagram_posts_still_one_post(self):
        job=self.desk.job(self.create_job('https://instagram.com/p/abc/'))
        build_command(job,self.desk.root,self.desk.cookie_for(job),self.desk.settings(),self.desk.state)
        conf=json.loads((self.desk.state/('gallery-'+job['id']+'.json')).read_text())
        self.assertEqual(conf['extractor']['instagram']['max-posts'],1)
        self.assertNotIn('archive',conf['extractor'])
        with self.assertRaises(APIError):self.create_job('https://instagram.com/someuser/')

    def test_validation_and_explicit_facebook_dependency(self):
        for value in ('../escape','https://evil.test/user','https://instagram.com.evil.test/user','https://instagram.com/p/123/','https://me:secret@instagram.com/user/','https://instagram.com:81/user/','con'):
            with self.subTest(value=value),self.assertRaises((ValueError,APIError)):
                self.desk.add_profile({'platform':'instagram','profile':value})
        with patch('profiles.browser_ready',return_value=False),self.assertRaisesRegex(APIError,'install-facebook'):
            self.desk.add_profile({'platform':'facebook','profile':'someuser'})
        with patch('profiles.browser_ready',return_value=True):
            result=self.desk.add_profile({'platform':'facebook','profile':'https://www.facebook.com/profile.php?id=12345','folder':'Collections','mode':'reels'})
        job=self.desk.job(result['id']);self.assertEqual(job['subdir'],'');self.assertEqual(job['folder'],'Collections')
        args=build_command(job,self.desk.staging(job['id']),self.desk.cookie_for(job),self.desk.settings(),self.desk.state)
        self.assertTrue(args[1].endswith('facebook_profile.py'))

    def test_empty_profile_failure_preserves_auth_error(self):
        result=self.desk.add_profile({'platform':'instagram','profile':'empty'})
        job=self.desk.job(result['id'])
        program='print("login_required: saved session rejected",flush=True);raise SystemExit(1)'
        with patch('server.validate_url'),patch('server.build_command',return_value=[sys.executable,'-c',program]):
            self.desk.run_job(job)
        fresh=self.desk.job(job['id']);self.assertEqual(fresh['error_kind'],'auth');self.assertEqual(fresh['status'],'error')

    def test_partial_profile_files_are_published_on_failure_and_counted(self):
        result=self.desk.add_profile({'platform':'instagram','profile':'example'})
        job=self.desk.job(result['id']);dest=self.desk.staging(job['id'])
        program=f'from pathlib import Path;Path({str(dest/"partial.jpg")!r}).write_bytes(b"image");print("ZEDITEM abc",flush=True);print("HTTP Error 429",flush=True);raise SystemExit(1)'
        with patch('server.validate_url'),patch('server.build_command',return_value=[sys.executable,'-c',program]):
            self.desk.run_job(job)
        fresh=self.desk.job(job['id']);self.assertEqual(fresh['error_kind'],'rate_limit');self.assertEqual(fresh['items'],1)
        self.assertTrue((self.desk.root/'Instagram/example/partial.jpg').is_file())


class CollectorTests(unittest.TestCase):
    def test_canonical_links_drop_unrelated_navigation_and_dedupe(self):
        self.assertEqual(canonical_link('https://www.facebook.com/example/posts/123?tracking=x','example')[1],'post-123')
        self.assertEqual(canonical_link('/reel/123?x=y','example','reels')[1],'reel-123')
        self.assertIsNone(canonical_link('/other/posts/123','example'))
        self.assertIsNone(canonical_link('https://evil.test/reel/123','example','reels'))
        self.assertIsNone(canonical_link('/reel/123','example','posts'))
        self.assertEqual(canonical_link('/photo.php?fbid=123&set=other','example')[2],'https://www.facebook.com/photo.php?fbid=123')

    def test_photos_are_bound_to_exact_id_and_allowed_media_host(self):
        docs=[{'data':[{'id':'other','image':{'uri':'https://scontent.fbcdn.net/wrong.jpg','width':4000,'height':4000}},
                      {'id':'123','image':{'uri':'https://scontent.fbcdn.net/right.jpg','width':2000,'height':1200}},
                      {'id':'123','image':{'uri':'https://evil.test/steal','width':8000,'height':8000}}]}]
        self.assertEqual(photo_source(docs,'123'),'https://scontent.fbcdn.net/right.jpg')
        self.assertIsNone(photo_source(docs,'missing'))
        for url in ('https://fbcdn.net.evil.test/a','http://scontent.fbcdn.net/a','file:///tmp/test','https://me:pw@scontent.fbcdn.net/a'):
            self.assertFalse(media_host(url))

    def test_profile_limits_require_attention_without_automatic_retry(self):
        for msg in ('Collection reached the configured scroll limit','No accessible media found'):
            self.assertEqual(platforms.failure_kind(msg)[0],'access')

    def test_facebook_stops_at_rate_limit_before_trying_next_video(self):
        import sqlite3,facebook_profile
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder);db=sqlite3.connect(dest/'.facebook-profile.sqlite3')
            db.execute('CREATE TABLE media(key TEXT PRIMARY KEY,kind TEXT,url TEXT,done INTEGER)')
            db.executemany('INSERT INTO media VALUES(?,?,?,0)',[('reel-1','video','https://facebook.com/reel/1'),('reel-2','video','https://facebook.com/reel/2')]);db.commit();db.close()
            class Process:
                stdout=io.StringIO('ERROR: HTTP Error 429: Too Many Requests\n')
                def wait(self):return 1
            with patch('facebook_profile.collect',return_value=(False,0)),patch('facebook_profile.subprocess.Popen',return_value=Process()) as run:
                with self.assertRaisesRegex(ValueError,'429'):facebook_profile.run({'dest':folder,'cookie':str(dest/'cookies-work.txt'),'media_kib':0})
                self.assertEqual(run.call_count,1)

    def test_local_address_configuration_preserves_other_avahi_options(self):
        old='[server]\n#host-name=old\nuse-ipv4=yes\n[reflector]\nenable-reflector=no\n'
        new=local_setup.avahi_config(old)
        self.assertEqual(new.count('host-name=zed'),1);self.assertIn('use-ipv4=yes',new);self.assertIn('enable-reflector=no',new)
        self.assertEqual(local_setup.avahi_config(new),new)
        units=local_setup.units('/usr/lib/systemd/systemd-socket-proxyd','127.0.0.1',8787)
        self.assertIn('ListenStream=80',units['zedair-local.socket'])
        self.assertIn('127.0.0.1:8787',units['zedair-local.service'])
        self.assertIn('DynamicUser=true',units['zedair-local.service'])


class ProfileHTTPTests(Fixture):
    request=rev.NewHTTPTests.request
    login=rev.NewHTTPTests.login
    setUp=prior.PreviewTests.setUp
    tearDown=prior.PreviewTests.tearDown

    def test_profile_api_auth_and_paged_files(self):
        self.assertEqual(self.request('/api/profiles',{'platform':'instagram','profile':'example'},cookie=False)[0],401)
        self.assertEqual(self.request('/api/system',cookie=False)[0],401)
        self.login()
        code,usage,_=self.request('/api/system')
        self.assertEqual(code,200);self.assertIn('cpu_percent',usage);self.assertIn('memory',usage)
        platforms.save_cookies(self.desk.state,'instagram',{'fields':{'sessionid':'fixture-session'}})
        status,result,_=self.request('/api/profiles',{'platform':'instagram','profile':'example','mode':'everything'})
        self.assertEqual(status,200);self.assertEqual(self.desk.job(result['id'])['profile_mode'],'everything')
        for i in range(31):(self.desk.root/f'photo-{i:02}.jpg').write_bytes(b'fixture')
        status,page,_=self.request('/api/folders?limit=24&offset=24')
        self.assertEqual(status,200);self.assertEqual(page['total'],31);self.assertEqual(len(page['entries']),7);self.assertFalse(page['more'])
        self.assertEqual(self.request('/api/folders?offset=-1')[0],400)


class SystemUsageTests(unittest.TestCase):
    def test_cpu_interval_uses_total_ticks_without_double_counting_guest(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'stat').write_text('cpu 100 0 100 800 0 0 0 0 50 0\n')
            (path/'meminfo').write_text('MemTotal: 2000 kB\nMemAvailable: 1500 kB\nSwapTotal: 1000 kB\nSwapFree: 750 kB\n')
            (path/'uptime').write_text('12345.5 9999')
            with patch('systemstats.time.monotonic',return_value=10):monitor=SystemStats(path,path/'missing')
            self.assertEqual(cpu_ticks((path/'stat').read_text()),(1000,800))
            (path/'stat').write_text('cpu 120 0 110 870 0 0 0 0 60 0\n')
            with patch('systemstats.time.monotonic',return_value=14):sample=monitor.snapshot()
            self.assertEqual(sample['cpu_percent'],30)
            self.assertEqual(sample['memory']['percent'],25);self.assertEqual(sample['swap']['percent'],25)
            self.assertEqual(sample['uptime_seconds'],12345.5);self.assertIsNone(sample['temperature_c']);self.assertIsNone(sample['ssd'])
            (path/'stat').write_text('cpu 1000 0 1000 1000 0 0 0 0\n')
            with patch('systemstats.time.monotonic',return_value=14.1):self.assertEqual(monitor.snapshot()['cpu_percent'],30)

    def test_missing_metrics_and_zero_swap_do_not_invent_usage(self):
        self.assertEqual(memory_stats('bad'),(None,None))
        ram,swap=memory_stats('MemTotal: 100 kB\nMemAvailable: 90 kB\nSwapTotal: 0 kB\n')
        self.assertEqual(swap['percent'],0);self.assertEqual(ram['used'],10240)
        with tempfile.TemporaryDirectory() as folder:
            result=SystemStats(folder,folder).snapshot()
            self.assertIsNone(result['cpu_percent']);self.assertIsNone(result['memory'])
