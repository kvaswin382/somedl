import datetime,json,subprocess,tempfile
from pathlib import Path
from unittest.mock import patch
from test_desk import Fixture
from app_time import checked
from fileops import rename_folder,finish_folder_rename
from storage_tools import stamp,set_cover
from remotes import merge_config
from publishing import facebook_title
from gallery_meter import Meter
import engine_update

class RevisionTests(Fixture):
    def setUp(self):
        super().setUp();self.desk.rpc.call=lambda *a,**k:{'torrents':[]}
    def test_timezone_exact_half_hour_day_boundary(self):
        self.desk.set_setting('timezone','Asia/Kolkata')
        from app_time import zone
        today=datetime.datetime.now(zone(self.desk)).date()
        start=datetime.datetime.combine(today,datetime.time(),tzinfo=zone(self.desk)).astimezone(datetime.timezone.utc)
        for minute,value in ((-1,17),(0,23),(59,41)):
            self.desk.write('INSERT INTO usage_hourly VALUES(?,?,?,?)',((start+datetime.timedelta(minutes=minute)).strftime('%Y-%m-%dT%H:%M'),'fixture',value,0))
        result=self.desk.usage_summary('day')
        self.assertEqual(result['timezone'],'Asia/Kolkata');self.assertEqual(result['total'],64);self.assertEqual(result['series'][0]['bytes'],64)
        self.assertEqual(checked('Pacific/Auckland'),'Pacific/Auckland')
        with self.assertRaises(ValueError):checked('../../etc/passwd')
    def test_import_preserves_existing_and_secret_percent(self):
        path=self.desk.remotes.config;path.write_text('[existing]\ntype = drive\ntoken = keep\n')
        result=merge_config(path,'[existing]\ntype=drive\ntoken=wrong\n[Gdrive]\ntype=drive\ntoken=abc%12\n')
        self.assertEqual(result,{'added':['Gdrive'],'skipped':['existing']});self.assertIn('keep',path.read_text());self.assertNotIn('wrong',path.read_text());self.assertIn('abc%12',path.read_text());self.assertEqual(path.stat().st_mode&0o777,0o600)
        before=path.read_text()
        with self.assertRaises(ValueError):merge_config(path,'[/bad:name]\ntype=drive\n')
        self.assertEqual(before,path.read_text())
    def test_folder_rename_preserves_bytes_cover_and_dates(self):
        root=self.desk.root/'Old';root.mkdir();(root/'Nested').mkdir();p=root/'cover.jpg';p.write_bytes(b'image')
        (root/'Nested/video.mp4').write_bytes(b'video')
        set_cover(self.desk,{'path':'Old/cover.jpg','stamp':stamp(p)})
        self.desk.write('INSERT INTO file_dates VALUES(?,?,?,?,?)',('Old/Nested','video.mp4',5,1,10))
        result=rename_folder(self.desk,{'path':'Old','name':'New','stamp':stamp(root)})
        self.assertEqual(result['path'],'New');self.assertEqual((self.desk.root/'New/Nested/video.mp4').read_bytes(),b'video')
        self.assertEqual(self.desk.rows('SELECT * FROM covers')[0]['folder'],'New')
        self.assertEqual(self.desk.rows('SELECT * FROM file_dates')[0]['folder'],'New/Nested')
        self.assertFalse((self.desk.state/'folder-rename.json').exists())
    def test_folder_rename_conflicts_and_active_jobs(self):
        root=self.desk.root/'Old';root.mkdir();(self.desk.root/'Other').mkdir()
        with self.assertRaises(ValueError):rename_folder(self.desk,{'path':'Old','name':'Other','stamp':stamp(root)})
        jid=self.create_job();self.desk.update_job(jid,folder='Old',subdir='')
        with self.assertRaisesRegex(ValueError,'queued download'):rename_folder(self.desk,{'path':'Old','name':'New','stamp':stamp(root)})
        self.assertTrue(root.exists())
    def test_rename_recovers_metadata_after_restart(self):
        old=self.desk.root/'Old';old.mkdir();st=old.stat();self.desk.write('INSERT INTO covers VALUES(?,?)',('Old','pic.jpg'))
        (self.desk.state/'folder-rename.json').write_text(json.dumps({'source':'Old','target':'New','identity':[st.st_dev,st.st_ino]}));old.rename(self.desk.root/'New')
        finish_folder_rename(self.desk);self.assertEqual(self.desk.rows('SELECT * FROM covers')[0]['folder'],'New')
    def test_torrent_counts_network_only_not_cached_size_and_persists_delta(self):
        torrent={'hashString':'a'*40,'addedDate':1,'downloadedEver':100,'uploadedEver':30,'haveValid':999999999,'totalSize':999999999,'percentDone':1}
        self.desk.record_owned_torrent_usage([torrent]);self.assertEqual(self.desk.usage_summary('day')['total'],0)
        torrent.update(downloadedEver=150,uploadedEver=35);self.desk.record_owned_torrent_usage([torrent]);self.desk.record_owned_torrent_usage([torrent])
        self.assertEqual(self.desk.usage_summary('day')['total'],50);self.assertEqual(self.desk.usage_summary('day')['uploaded'],5)
        self.desk.set_setting('new_torrent_usage',['b'*40]);torrent.update(hashString='b'*40,downloadedEver=17,uploadedEver=0)
        self.desk.record_owned_torrent_usage([torrent]);self.assertEqual(self.desk.usage_summary('day')['total'],67)
    def test_gallery_meter_flush_small_failed_transfer(self):
        result=[];m=Meter(result.append);m.add(16);m.add(42);m.flush();m.flush();self.assertEqual(result,[58])
    def test_facebook_metadata_name_and_caption(self):
        self.assertEqual(facebook_title({'uploader':'Sample Creator','description':'A lovely day','title':'Facebook'}),'Sample Creator - A lovely day')
        self.assertEqual(facebook_title({'title':'Facebook','id':'123'}),'Facebook - 123')
        self.assertEqual(facebook_title({'title':'Caption | Facebook'}),'Caption')
    def test_updater_stop_failure_is_reported_without_pip_or_false_rollback(self):
        calls=[];status=self.base/'update.json'
        def run(args,**kwargs):
            calls.append(args)
            if args==['systemctl','stop','zedair']:raise subprocess.CalledProcessError(1,args,stderr='Job for zedair.service canceled.')
            return subprocess.CompletedProcess(args,0,stdout='',stderr='')
        with patch.object(engine_update,'status_path',return_value=status),patch.object(engine_update,'UPDATE_LOCK',self.base/'update-lock'),patch.object(engine_update.importlib.metadata,'version',return_value='1.0'),patch.object(engine_update.subprocess,'run',side_effect=run):engine_update.update('yt-dlp')
        saved=json.loads(status.read_text());self.assertEqual(saved['status'],'error');self.assertIn('canceled',saved['detail']);self.assertFalse(any('pip' in c for c in calls));self.assertEqual(calls[-1],['systemctl','start','zedair-local.socket'])
