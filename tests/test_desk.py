import base64
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import APIError, Desk, Handler, Server, digest, safe_relative, password_hash, password_ok
from torrent_engine import QBittorrent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from torrent_fixtures import FakeQBittorrent
from media import build_command, engine_for, threads_media, validate_url


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        mount = self.base/'ssd'
        (mount/'Zedair').mkdir(parents=True)
        self.config = {'state': str(self.base/'state'), 'mount': str(mount), 'require_mount': False,
                       'setup_key_hash': digest('installation-key')}
        self.desk = Desk(self.config)
        self.desk.set_setting('reserve_gib', 0)

    def tearDown(self):
        self.desk.close()
        self.tmp.cleanup()

    def create_job(self, url='https://www.youtube.com/watch?v=fixture'):
        return self.desk.add_jobs({'urls':[url]})['ids'][0]


class CoreTests(Fixture):
    def test_paths_reject_traversal_and_windows_bad_names(self):
        for value in ('../escape','/absolute','x/../../escape','x\\..\\escape','a//b','CON','NUL.txt','bad:folder','.hidden','name.','a/../b'):
            with self.subTest(value=value), self.assertRaises(APIError):
                safe_relative(value)
        self.assertEqual(safe_relative('Family/2026 photos'), 'Family/2026 photos')

    def test_symlink_cannot_escape_root(self):
        (self.desk.root/'escape').symlink_to(self.base)
        with self.assertRaises(APIError):
            self.desk.destination('escape/state')

    def test_missing_mount_never_creates_download_folder(self):
        self.desk.config['require_mount'] = True
        self.assertFalse(self.desk.storage()['ready'])
        with self.assertRaises(APIError):
            self.desk.destination('Videos/new', create=True)
        self.assertFalse((self.desk.root/'Videos').exists())

    def test_mount_identity_required(self):
        self.desk.config['require_mount'] = True
        with patch('server.os.path.ismount', return_value=True):
            self.assertIn('identity', self.desk.storage()['reason'])
            self.desk.config['ssd_uuid'] = 'not-a-real-disk'
            self.assertIn('does not match', self.desk.storage()['reason'])

    def test_low_space_blocks_writes(self):
        self.desk.set_setting('reserve_gib', 1000000)
        self.assertFalse(self.desk.storage()['ready'])
        with self.assertRaises(APIError):
            self.desk.destination('new', create=True)

    def test_batch_validation_is_atomic_and_duplicates_skipped(self):
        with self.assertRaises(ValueError):
            self.desk.add_jobs({'urls':['https://youtube.com/watch?v=1', 'file:///etc/passwd']})
        self.assertEqual(self.desk.rows('SELECT id FROM jobs'), [])
        first = self.create_job()
        again = self.desk.add_jobs({'urls':['https://www.youtube.com/watch?v=fixture']})
        self.assertEqual(again['skipped'], 1)
        self.assertEqual(len(self.desk.rows('SELECT id FROM jobs')), 1)
        self.desk.job_action(first, 'cancel')
        self.assertNotEqual(first, self.create_job())

    def test_state_survives_restart_and_recovers_running_job(self):
        jid = self.create_job()
        self.desk.update_job(jid, status='running', attempts=1)
        self.desk.set_setting('hold', True)
        self.desk.close()
        self.desk = Desk(self.config)
        self.assertEqual(self.desk.job(jid)['status'], 'queued')
        self.assertTrue(self.desk.settings()['hold'])

    def test_password_hash_is_salted_and_checks(self):
        a, b = password_hash('a long password'), password_hash('a long password')
        self.assertNotEqual(a, b)
        self.assertTrue(password_ok('a long password', a))
        self.assertFalse(password_ok('wrong password', a))

    def test_cookie_routing_does_not_choose_instagram_for_youtube(self):
        (self.desk.state/'cookies-instagram.txt').write_text('test')
        j = self.desk.job(self.create_job())
        self.assertIsNone(self.desk.cookie_for(j))
        j = self.desk.job(self.create_job('https://www.instagram.com/p/fixture/'))
        self.assertEqual(self.desk.cookie_for(j).name, 'cookies-instagram.txt')

    def test_retry_worker_then_manual_pause(self):
        jid = self.create_job()
        command = [sys.executable, '-c', "print('HTTP 503 test failure');raise SystemExit(1)"]
        with patch('server.validate_url'), patch('server.build_command', return_value=command):
            self.desk.run_job(self.desk.job(jid))
        job = self.desk.job(jid)
        self.assertEqual(job['status'], 'retry')
        self.assertEqual(job['attempts'], 1)
        self.assertGreater(job['next_try'], time.time()+40)
        self.assertIn('503', job['last_error'])
        self.desk.job_action(jid, 'pause')
        self.assertEqual(self.desk.job(jid)['status'], 'paused')

    def test_worker_completes_and_persists_title(self):
        jid = self.create_job()
        command = [sys.executable, '-c', "from pathlib import Path;Path('Fixture movie.mp4').write_bytes(b'movie');print('ZEDTITLE Fixture movie');print('ZEDPROG {\"downloaded_bytes\":100,\"total_bytes\":100}')"]
        with patch('server.validate_url'), patch('server.build_command', return_value=command):
            self.desk.run_job(self.desk.job(jid))
        job = self.desk.job(jid)
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['name'], 'Fixture movie')
        self.assertEqual(job['progress'], 100)

    def test_unplug_stops_worker_and_does_not_claim_success(self):
        jid = self.create_job()
        command = [sys.executable, '-c', 'import time;time.sleep(30)']
        with patch('server.validate_url'), patch('server.build_command', return_value=command):
            thread = threading.Thread(target=self.desk.run_job, args=(self.desk.job(jid),))
            thread.start()
            for _ in range(50):
                if self.desk.process:
                    break
                time.sleep(.02)
            self.desk.config['require_mount'] = True
            thread.join(8)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.desk.job(jid)['status'], 'blocked')

    def test_command_arguments_and_destination(self):
        job = self.desk.job(self.create_job())
        job.update(quality='1080', container='auto', playlist=0, subtitles=1)
        dest = self.desk.root/'test'
        args = build_command(job, dest, None, self.desk.settings(), self.desk.state)
        self.assertIn('bv*[height<=1080]+ba/b[height<=1080]', args)
        self.assertIn('--no-playlist', args)
        self.assertIn('--no-overwrites', args)
        self.assertIn('temp:'+str(dest), args)
        self.assertEqual(args[-2], '--')
        self.assertEqual(args[-1], job['url'])


class ThreadsTests(unittest.TestCase):
    def test_exact_post_all_carousel_items_and_best_variants(self):
        data = {'unrelated':{'code':'other','image_versions2':{'candidates':[{'url':'https://cdn.example/wrong.jpg'}]}},
                'post':{'code':'target','carousel_media':[
                {'image_versions2':{'candidates':[{'url':'https://cdn.example/small.jpg','width':100,'height':100},
                                                 {'url':'https://cdn.example/big.jpg','width':2000,'height':1200}]}},
                {'video_versions':[{'url':'https://cdn.example/low.mp4','width':640,'height':360},
                                   {'url':'https://cdn.example/high.mp4','width':1920,'height':1080}]}]}}
        page='<script type="application/json">'+json.dumps(data)+'</script>'
        self.assertEqual(threads_media(page,'target'), [('https://cdn.example/big.jpg','jpg'),('https://cdn.example/high.mp4','mp4')])

    def test_missing_exact_post_does_not_download_profile_thumbnail(self):
        with self.assertRaises(ValueError):
            threads_media('<meta property="og:image" content="profile.jpg"><script type="application/json">{"code":"other"}</script>', 'target')

    def test_url_validation_and_engine_routing(self):
        for url in ('file:///etc/passwd','http://127.0.0.1/private','https://user:pass@example.com','http://10.0.0.1/','https://example.com:9092'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url, dns=False)
        self.assertEqual(engine_for('https://www.threads.com/@user/post/abc'), 'threads')
        self.assertEqual(engine_for('https://www.instagram.com/reel/abc'), 'instagram')
        self.assertEqual(engine_for('https://instagram.com.evil.example/video'), 'video')


class HTTPTests(Fixture):
    def setUp(self):
        super().setUp()
        self.server = Server(('127.0.0.1',0), Handler)
        self.server.desk = self.desk
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ''
        self.csrf = ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def request(self, path, data=None, csrf=True, cookie=True, extra=None, binary=False):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        headers={'Content-Type':'application/json'}
        if cookie: headers['Cookie']=self.cookie
        if csrf: headers['X-Zedair-CSRF']=self.csrf
        headers.update(extra or {})
        conn.request('GET' if data is None else 'POST', path, body=None if data is None else json.dumps(data),headers=headers)
        response=conn.getresponse()
        status=response.status
        set_cookie=response.getheader('Set-Cookie')
        raw=response.read()
        if set_cookie: self.cookie=set_cookie.split(';')[0]
        result=raw if binary else json.loads(raw)
        conn.close()
        return status, result, set_cookie

    def login(self):
        status, result, cookie=self.request('/api/setup',{'setup_key':'installation-key','username':'owner','password':'long-test-password'})
        self.assertEqual(status,200)
        self.csrf=result['csrf']
        return cookie

    def test_first_run_setup_key_and_35_day_session(self):
        status, body, _=self.request('/api/auth')
        self.assertTrue(body['setup'])
        status, _, _=self.request('/api/setup',{'setup_key':'wrong','username':'owner','password':'long-test-password'})
        self.assertEqual(status,403)
        cookie=self.login()
        self.assertIn('Max-Age=3024000',cookie)
        self.assertIn('HttpOnly',cookie)
        self.assertIn('SameSite=Strict',cookie)
        status, _, _=self.request('/api/setup',{'setup_key':'installation-key','username':'second','password':'long-test-password'})
        self.assertEqual(status,409)
        status, body, _=self.request('/api/auth')
        self.assertTrue(body['authenticated'])

    def test_unauthenticated_data_and_csrf_blocked(self):
        self.assertEqual(self.request('/api/state',cookie=False)[0],401)
        self.login()
        self.assertEqual(self.request('/api/hold',{'hold':True},csrf=False)[0],403)
        self.assertEqual(self.request('/api/hold',{'hold':True},extra={'Origin':'https://evil.example'})[0],403)
        self.assertFalse(self.desk.get_setting('hold',False))

    def test_folders_and_range_download(self):
        self.login()
        self.assertEqual(self.request('/api/folders',{'path':'Photos/2026'})[0],200)
        file=self.desk.root/'Photos/2026/test.txt'
        file.write_bytes(b'0123456789')
        status, raw, _=self.request('/api/file?path=Photos/2026/test.txt',extra={'Range':'bytes=2-5'},binary=True)
        self.assertEqual((status,raw),(206,b'2345'))
        self.assertEqual(self.request('/api/folders',{'path':'../state'})[0],400)

    def test_cookie_import_is_private_and_not_returned(self):
        self.login()
        content='# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t2000000000\tsessionid\tfixture-only\n'
        self.assertEqual(self.request('/api/cookies',{'slot':'instagram','content':content})[0],200)
        self.assertEqual((self.desk.state/'cookies-instagram.txt').stat().st_mode&0o777,0o600)
        status, body, _=self.request('/api/state')
        self.assertNotIn('fixture-only',json.dumps(body))
        self.assertTrue(body['cookies']['instagram'])
        self.request('/api/cookies',{'slot':'instagram','delete':True})
        self.assertFalse((self.desk.state/'cookies-instagram.txt').exists())

    def test_password_change_revokes_all_sessions(self):
        self.login()
        self.request('/api/password',{'old':'long-test-password','password':'another-long-password'})
        self.assertEqual(self.request('/api/state')[0],401)
        status, body, _=self.request('/api/login',{'username':'owner','password':'another-long-password'})
        self.assertEqual(status,200)

    def engine(self):
        fake = FakeQBittorrent()
        self.addCleanup(fake.close)
        self.desk.bt = QBittorrent(fake.config(), APIError)
        return fake

    def prio(self, fake):
        return [(c[1]['id'], c[1]['priority']) for c in fake.calls if c[0] == 'torrents/filePrio']

    def test_torrent_file_selection_never_sends_an_empty_list(self):
        self.login();fake=self.engine()
        fake.add_torrent('a'*40,save_path=str(self.desk.root),files=2)
        self.assertEqual(self.request('/api/torrent/action',{'id':'a'*40,'action':'files','wanted':[]})[0],200)
        self.assertEqual(self.prio(fake),[('0|1','0')])
        self.assertEqual(self.request('/api/torrent/action',{'id':'a'*40,'action':'files','wanted':[1]})[0],200)
        self.assertEqual(self.prio(fake)[1:],[('1','1'),('0','0')])

    def test_torrent_file_selection_validates_indexes(self):
        self.login();fake=self.engine()
        fake.add_torrent('a'*40,save_path=str(self.desk.root),files=2)
        for wanted in ([2],[-1],['0'],[True],'0'):
            self.assertEqual(self.request('/api/torrent/action',{'id':'a'*40,'action':'files','wanted':wanted})[0],400,wanted)
        self.assertEqual(self.prio(fake),[])

    def test_torrent_cannot_manage_outside_root(self):
        self.login();fake=self.engine()
        fake.add_torrent('a'*40,save_path='/home/other')
        for action in ('remove','pause','resume','verify','files'):
            self.assertEqual(self.request('/api/torrent/action',{'id':'a'*40,'action':action,'wanted':[0]})[0],404,action)
        self.assertEqual(self.request('/api/torrent?id='+'a'*40)[0],404)
        self.assertEqual([p for p in fake.paths() if p not in ('torrents/info','app/webapiVersion')],[])

    def test_bulk_identifiers_are_refused_before_reaching_the_engine(self):
        self.login();fake=self.engine()
        fake.add_torrent('a'*40,save_path=str(self.desk.root))
        for ident in ('all','a'*40+'|'+'b'*40,'',None):
            self.assertEqual(self.request('/api/torrent/action',{'id':ident,'action':'remove'})[0],404,ident)
        self.assertEqual(fake.calls,[])

    def test_remove_keeps_downloaded_files(self):
        self.login();fake=self.engine()
        fake.add_torrent('a'*40,save_path=str(self.desk.root))
        self.assertEqual(self.request('/api/torrent/action',{'id':'a'*40,'action':'remove'})[0],200)
        self.assertEqual([c[1]['deleteFiles'] for c in fake.calls if c[0]=='torrents/delete'],['false'])

    def test_adding_torrents_validates_input_and_honours_hold(self):
        self.login();fake=self.engine()
        good='magnet:?xt=urn:btih:'+'c'*40
        status,body,_=self.request('/api/torrents',{'magnet':good})
        self.assertEqual((status,body['hash']),(200,'c'*40))
        for payload in ({'magnet':'http://example.com'},{'magnet':'magnet:?dn=x'},{'metainfo':'!!!'},{'metainfo':base64.b64encode(b'not bencode').decode()},{}):
            self.assertEqual(self.request('/api/torrents',payload)[0],400,payload)
        self.desk.set_setting('hold',True)
        self.assertEqual(self.request('/api/torrents',{'magnet':good})[0],409)
        self.assertEqual(fake.paths().count('torrents/add'),1)

    def test_pause_and_resume_work_and_resume_needs_the_queue_released(self):
        self.login();fake=self.engine()
        fake.add_torrent('a'*40,save_path=str(self.desk.root))
        self.assertEqual(self.request('/api/torrent/action',{'id':'a'*40,'action':'resume'})[0],200)
        self.assertEqual(self.request('/api/torrent/action',{'id':'a'*40,'action':'pause'})[0],200)
        self.desk.set_setting('hold',True)
        self.assertEqual(self.request('/api/torrent/action',{'id':'a'*40,'action':'resume'})[0],409)

    def test_hold_stops_active_torrents_and_release_restarts_only_those(self):
        fake=self.engine()
        fake.add_torrent('a'*40,save_path=str(self.desk.root),state='downloading')
        fake.add_torrent('b'*40,save_path=str(self.desk.root),state='pausedUP')
        self.desk.set_setting('hold',True)
        thread=threading.Thread(target=self.desk.torrent_loop,daemon=True);thread.start()
        def wait(path):
            for _ in range(200):
                if path in fake.paths():return [c for c in fake.calls if c[0]==path][-1]
                time.sleep(0.02)
            self.fail('engine never received '+path)
        self.assertEqual(wait('torrents/pause')[1]['hashes'],'a'*40)
        self.assertEqual(self.desk.get_setting('blocked_torrents'),['a'*40])
        self.desk.set_setting('hold',False);self.desk.torrent_wake.set()  # wake: no 15 s idle wait
        self.assertEqual(wait('torrents/resume')[1]['hashes'],'a'*40)
        for _ in range(100):
            if self.desk.get_setting('blocked_torrents')==[]:break
            time.sleep(0.02)
        self.assertEqual(self.desk.get_setting('blocked_torrents'),[])
        self.desk.stop.set();self.desk.torrent_wake.set();thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_snapshot_lists_only_torrents_inside_the_zedair_root(self):
        fake=self.engine()
        fake.add_torrent('a'*40,save_path=str(self.desk.root/'Torrents'),state='downloading',progress=0.5)
        fake.add_torrent('b'*40,save_path='/home/other')
        self.assertEqual([t['hashString'] for t in self.desk.refresh_torrents()],['a'*40])
        self.assertEqual(self.desk.snapshot()['torrents'][0]['percentDone'],0.5)

    def test_saved_settings_reapply_even_when_engine_offline(self):
        self.login()
        data=self.desk.settings();data['torrent_up_kib']=100;data['reserve_gib']=1
        with patch.object(self.desk.bt,'apply_settings',side_effect=APIError('offline',503)):
            self.assertEqual(self.request('/api/settings',data)[0],200)
        self.assertEqual(self.desk.settings()['torrent_up_kib'],100)

    def test_saving_torrent_settings_pushes_them_to_the_engine(self):
        self.login();fake=self.engine()
        data=self.desk.settings();data['torrent_down_kib']=300;data['seed_ratio']=2.0;data['reserve_gib']=1
        self.assertEqual(self.request('/api/settings',data)[0],200)
        sent=json.loads([c for c in fake.calls if c[0]=='app/setPreferences'][-1][1]['json'])
        self.assertEqual((sent['dl_limit'],sent['max_ratio']),(300*1024,2.0))

    def test_deleting_a_file_owned_by_a_torrent_is_refused(self):
        self.login();fake=self.engine()
        (self.desk.root/'Torrents'/'Pack').mkdir(parents=True)
        path=self.desk.root/'Torrents'/'Pack'/'f0.bin';path.write_bytes(b'x')
        fake.add_torrent('a'*40,name='Pack',save_path=str(self.desk.root/'Torrents'),files=1)
        fake.files['a'*40][0]['name']='Pack/f0.bin'
        st=path.stat()
        with self.assertRaisesRegex(Exception,'Remove this torrent'):
            self.desk.delete_file({'confirm':True,'path':'Torrents/Pack/f0.bin','stamp':f'{st.st_size}:{st.st_mtime_ns}'})
        self.assertTrue(path.exists())


if __name__=='__main__':
    unittest.main(verbosity=2)
