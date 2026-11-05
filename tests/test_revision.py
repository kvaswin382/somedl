import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_desk import Fixture
import test_desk as baseline
import platforms
from media import build_command
from publishing import publish, fingerprint, filename
from speedcheck import parse_result

class RevisionTests(Fixture):
    def test_platform_routing_defaults_and_no_random_subfolders(self):
        for url,folder in [('https://youtu.be/abc','YouTube'),('https://instagram.com/p/abc/','Instagram'),('https://x.com/user/status/123','X'),('https://twitter.com/user/status/456','X'),('https://threads.com/@u/post/abc','Threads'),('https://example.com/movie','example.com')]:
            j=self.desk.job(self.create_job(url));self.assertEqual(j['subdir'],folder);self.assertEqual(j['layout'],2)
        self.assertEqual(platforms.platform_for('https://x.com.evil.test/a'),'other')
        p=self.desk.platform_settings();p['x']['quality']='720';p['x']['folder']='Social/X';self.desk.set_setting('platforms',p)
        j=self.desk.job(self.create_job('https://x.com/user/status/789'));self.assertEqual(j['quality'],'720');self.assertEqual(j['subdir'],'Social/X')
        args=build_command(j,self.desk.root,None,{'platforms':p,**self.desk.settings()},self.desk.state)
        self.assertIn('twitter:api=graphql',args)

    def test_session_cookie_roundtrip_expiry_and_redaction(self):
        platforms.save_cookies(self.desk.state,'instagram',{'fields':{'sessionid':'fixture-private-session','csrftoken':'fixture-csrf'}})
        self.assertTrue(platforms.cookie_status(self.desk.state,'instagram')['usable'])
        self.assertTrue(all(c.expires is None for c in platforms.load_jar(self.desk.state/'cookies-instagram.txt')))
        platforms.save_cookies(self.desk.state,'x',{'fields':{'auth_token':'fixture-auth-token','ct0':'fixture-ct0'}})
        x=platforms.load_jar(self.desk.state/'cookies-x.txt');self.assertEqual(len(list(x)),4)
        self.assertNotIn('fixture-auth-token',platforms.redact('error fixture-auth-token',self.desk.state))
        self.assertNotIn('fixture-private-session',json.dumps(self.desk.snapshot()))
        with self.assertRaises(ValueError):platforms.save_cookies(self.desk.state,'x',{'fields':{'auth_token':'one'}})
        platforms.save_cookies(self.desk.state,'instagram',{'content':'# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t1\tsessionid\told\n'})
        self.assertFalse(platforms.cookie_status(self.desk.state,'instagram')['usable'])

    def test_auth_errors_do_not_automatically_retry(self):
        jid=self.create_job('https://instagram.com/p/abc/')
        with patch('server.validate_url'),patch('server.build_command') as build:
            self.desk.run_job(self.desk.job(jid));build.assert_not_called()
        self.assertEqual(self.desk.job(jid)['status'],'error');self.assertEqual(self.desk.job(jid)['error_kind'],'auth')
        for message,kind in [('HTTP Error 429','rate_limit'),('ERROR login required','auth'),('HTTP 503','transient'),('HTTP Error 403','access')]:
            self.assertEqual(platforms.failure_kind(message)[0],kind)

    def test_numbered_titles_and_publish_restart(self):
        jid=self.create_job();j=self.desk.job(jid);stage=self.desk.staging(jid);out=self.desk.destination('YouTube',create=True)
        (out/'My movie.mp4').write_bytes(b'existing')
        for index in (1,2):
            (stage/f'raw{index}.mp4').write_bytes(('new'+str(index)).encode())
            (stage/f'raw{index}.info.json').write_text(json.dumps({'title':'My movie'}))
        publish(self.desk,j,stage,out)
        self.assertEqual((out/'My movie.mp4').read_bytes(),b'existing')
        self.assertEqual((out/'My movie (1).mp4').read_bytes(),b'new1')
        self.assertEqual((out/'My movie (2).mp4').read_bytes(),b'new2')
        publish(self.desk,j,stage,out);self.assertEqual(len(list(out.iterdir())),3)
        self.assertEqual(filename('CON'),'_CON');self.assertNotIn('/',filename('title/a:b'))

    def test_restart_between_rename_and_commit(self):
        jid=self.create_job();j=self.desk.job(jid);stage=self.desk.staging(jid);out=self.desk.destination('YouTube',create=True)
        source=stage/'test.mp4';source.write_bytes(b'media')
        sig=fingerprint(source);os.replace(source,out/'Title.mp4')
        self.desk.write('INSERT INTO published VALUES (?,?,?,?,0)',(jid,'test.mp4','Title.mp4',sig))
        self.desk.update_job(jid,phase='publish')
        with patch('server.validate_url'),patch('server.build_command') as build:
            self.desk.run_job(self.desk.job(jid));build.assert_not_called()
        self.assertEqual(self.desk.job(jid)['status'],'done');self.assertEqual(len(list(out.iterdir())),1)

    def test_deletion_protection_and_file_change(self):
        path=self.desk.root/'movie.mp4';path.write_bytes(b'data')
        data={'path':'movie.mp4','confirm':True,'stamp':str(path.stat().st_size)+':'+str(path.stat().st_mtime_ns)}
        with patch.object(self.desk.rpc,'call',return_value={'torrents':[{'downloadDir':str(self.desk.root),'files':[{'name':'movie.mp4'}]}]}):
            with self.assertRaisesRegex(Exception,'Remove this torrent'):self.desk.delete_file(data)
        self.assertTrue(path.exists())
        with patch.object(self.desk.rpc,'call',return_value={'torrents':[]}):self.desk.delete_file(data)
        self.assertFalse(path.exists())
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(Exception,'changed'):self.desk.delete_file(data)
        path.unlink();path.symlink_to(self.base/'outside')
        with self.assertRaises(Exception):self.desk.delete_file(data)

    def test_staging_symlink_rejected_and_legacy_job_preserved(self):
        j=self.desk.job(self.create_job())
        (self.desk.root/'.incomplete').symlink_to(self.base)
        with self.assertRaises(Exception):self.desk.staging(j['id'])
        self.desk.update_job(j['id'],layout=1,subdir='video-oldid')
        self.desk.close();self.desk=type(self.desk)(self.config)
        self.assertEqual(self.desk.job(j['id'])['subdir'],'video-oldid')

class NewHTTPTests(Fixture):
    request=baseline.HTTPTests.request
    login=baseline.HTTPTests.login
    def setUp(self):
        super().setUp()
        from server import Server, Handler
        import threading
        self.server=Server(("127.0.0.1",0),Handler);self.server.desk=self.desk
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.cookie="";self.csrf=""
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()
    def test_revision_api_auth_and_validation(self):
        self.assertEqual(self.request('/api/speedtest',{'action':'start'},cookie=False)[0],401)
        self.login()
        with patch.object(self.desk.speedtest,'start') as start:
            self.assertEqual(self.request('/api/speedtest',{'action':'start'},csrf=False)[0],403)
            start.assert_not_called()
            self.assertEqual(self.request('/api/speedtest',{'action':'start'})[0],200);start.assert_called_once()
        self.assertEqual(self.request('/api/cookies',{'slot':'x','fields':{'auth_token':'test-token','ct0':'test-csrf'}})[0],200)
        values=self.desk.platform_settings()['x'];values['folder']='../escape'
        self.assertEqual(self.request('/api/platforms',{'platform':'x','values':values})[0],400)
        self.assertEqual(self.request('/api/file/delete',{'path':'missing','confirm':False})[0],400)

