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
from server import APIError, Desk, Handler, Server, digest, safe_relative, password_hash, password_ok, Transmission
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
        job.update(quality='1080', playlist=0, subtitles=1)
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

    def test_torrent_file_selection_omits_empty_array(self):
        self.login()
        calls=[]
        def call(method,args=None):
            calls.append((method,args))
            if method=='torrent-get':return {'torrents':[{'hashString':'test','downloadDir':str(self.desk.root),'files':[{},{}]}]}
            return {}
        with patch.object(self.desk.rpc,'call',side_effect=call):
            self.assertEqual(self.request('/api/torrent/action',{'id':'test','action':'files','wanted':[]})[0],200)
        args=calls[-1][1]
        self.assertNotIn('files-wanted',args)
        self.assertEqual(args['files-unwanted'],[0,1])

    def test_torrent_cannot_manage_outside_root(self):
        self.login()
        with patch.object(self.desk.rpc,'call',return_value={'torrents':[{'downloadDir':'/home/other','files':[]}]}):
            self.assertEqual(self.request('/api/torrent/action',{'id':'test','action':'remove'})[0],404)

    def test_saved_settings_reapply_even_when_engine_offline(self):
        self.login()
        data=self.desk.settings();data['torrent_up_kib']=100;data['reserve_gib']=1
        with patch.object(self.desk.rpc,'call',side_effect=APIError('offline',503)):
            self.assertEqual(self.request('/api/settings',data)[0],200)
        self.assertEqual(self.desk.settings()['torrent_up_kib'],100)

class RPCHandshakeTests(unittest.TestCase):
    def test_transmission_session_handshake_and_auth(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        calls=[]
        class RPCHandler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((dict(self.headers),body))
                if self.headers.get('X-Transmission-Session-Id')!='fixture-session':
                    self.send_response(409);self.send_header('X-Transmission-Session-Id','fixture-session');self.end_headers();return
                raw=json.dumps({'result':'success','arguments':{'version':'fixture'}}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),RPCHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=Transmission({'rpc_url':f'http://127.0.0.1:{server.server_port}/transmission/rpc','rpc_user':'fixture','rpc_password':'fixture'})
            self.assertEqual(client.call('session-get')['version'],'fixture')
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[1][1]['method'],'session-get')
            self.assertTrue(calls[1][0]['Authorization'].startswith('Basic '))
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    unittest.main(verbosity=2)
