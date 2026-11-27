import json,subprocess,sys
from unittest.mock import patch
from test_desk import Fixture
from media import build_command

class FreshRetryTests(Fixture):
    def test_failed_publish_retry_reextracts_and_increments_attempt(self):
        jid=self.create_job('https://example.com/image')
        self.desk.update_job(jid,kind='instagram',cookie_profile='none',status='error',attempts=4,phase='publish',progress=90,items=5)
        stage=self.desk.staging(jid);(stage/'.profile-checkpoint-v1.json').write_text('stale');(stage/'partial.part').write_bytes(b'bad')
        saved=self.desk.root/'finished.jpg';saved.write_bytes(b'keep')
        self.desk.write('INSERT INTO published VALUES(?,?,?,?,?)',(jid,'missing.jpg','finished.jpg','bad',0))
        self.desk.job_action(jid,'retry');job=self.desk.job(jid)
        self.assertEqual(job['attempts'],4);self.assertEqual(job['retry_base'],4);self.assertEqual(job['phase'],'download')
        command=[sys.executable,'-c',"from pathlib import Path; assert not Path('partial.part').exists(); assert not Path('.profile-checkpoint-v1.json').exists(); Path('fresh.jpg').write_bytes(b'\\xff\\xd8\\xffimage')"]
        prefs=self.desk.platform_settings();prefs['instagram']['require_cookies']=False;self.desk.set_setting('platforms',prefs)
        with patch('server.validate_url'),patch('server.build_command',return_value=command) as build:
            self.desk.run_job(job);build.assert_called_once()
        job=self.desk.job(jid);self.assertEqual(job['status'],'done',job['last_error']);self.assertEqual(job['attempts'],5);self.assertEqual(job['restart_pending'],0)
        self.assertEqual(saved.read_bytes(),b'keep')
    def test_paused_resume_preserves_partials_and_attempt_history(self):
        jid=self.create_job();self.desk.update_job(jid,status='paused',attempts=2)
        p=self.desk.staging(jid)/'video.part';p.write_bytes(b'partial')
        self.desk.job_action(jid,'retry');j=self.desk.job(jid);self.desk.prepare_fresh_retry(j)
        self.assertEqual(p.read_bytes(),b'partial');self.assertEqual(j['attempts'],2);self.assertEqual(j['restart_pending'],0)
    def test_fresh_retry_has_new_automatic_retry_budget(self):
        jid=self.create_job();self.desk.update_job(jid,status='error',attempts=20);self.desk.job_action(jid,'retry')
        with patch('server.validate_url'),patch('server.build_command',return_value=[sys.executable,'-c',"print('Connection reset by peer');raise SystemExit(1)"]):self.desk.run_job(self.desk.job(jid))
        j=self.desk.job(jid);self.assertEqual(j['attempts'],21);self.assertEqual(j['status'],'retry',j['last_error'])
    def test_gallery_cache_is_scoped_to_partial_workspace(self):
        jid=self.create_job('https://www.instagram.com/p/fixture/');j=self.desk.job(jid);dest=self.desk.staging(jid)
        build_command(j,dest,None,self.desk.settings(),self.desk.state)
        config=json.loads((self.desk.state/('gallery-'+jid+'.json')).read_text())
        self.assertEqual(config['cache']['file'],str(dest/'.gallery-cache.sqlite3'))
