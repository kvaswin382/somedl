import base64
import datetime
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_desk import Fixture
from storage_tools import duplicates,delete_duplicates,delete_batch,upload,stamp,set_cover
from remotes import relative
from schedules import matches,ZONE,fields
from url_input import clean
from reddit_media import from_post


class InputTests(unittest.TestCase):
    def test_urls_from_prose_deduplicate_without_losing_signed_query(self):
        self.assertEqual(clean('hello https://example.com/a?q=2&token=abc.\nhttps://example.com/a?q=2&token=abc#x\nbye'),['https://example.com/a?q=2&token=abc'])
        self.assertEqual(clean('just text'),[])
        self.assertEqual(clean('https://user:pass@example.com/'),[])
    def test_remote_path_confined(self):
        for path in ('../x','a/../b','/tmp','other:folder','a\\b','a//b','a\n'):
            with self.subTest(path=path),self.assertRaises(ValueError):relative(path)
        self.assertEqual(relative('a b/🎬/folder'),'a b/🎬/folder')
    def test_schedule_timezone_and_calendar(self):
        dt=datetime.datetime(2026,11,19,21,30,tzinfo=datetime.timezone.utc)
        self.assertTrue(matches('0 3 * * *',dt)) # 03:00 next day in Kolkata
        self.assertFalse(matches('0 21 * * *',dt))
        self.assertTrue(matches('*/15 3 20 11 *',dt))
        self.assertFalse(matches('0 3 19 11 *',dt))
        for expr in ('@reboot','0 0 * * * rm -rf /','60 * * * *','*/0 * * * *','* * * * 7'):
            with self.assertRaises(ValueError):fields(expr)
    def test_reddit_only_exact_post_highest_manifest(self):
        post={'id':'abc','title':'Post','secure_media':{'reddit_video':{'dash_url':'https://v.redd.it/id/DASHPlaylist.mpd','fallback_url':'https://v.redd.it/id/low.mp4'}}}
        self.assertIn('DASHPlaylist',from_post(post,'abc')[0]['url'])
        with self.assertRaises(ValueError):from_post(post,'wrong')


class StorageTests(Fixture):
    def setUp(self):
        super().setUp();self.desk.rpc.call=lambda *a,**k:{'torrents':[]}
    def task(self,kind):
        key=self.desk.tasks.submit(kind,lambda j:None)['id'];return self.desk.tasks.jobs[key]
    def test_duplicates_keep_newest_among_arbitrary_names(self):
        for name,age in [('a.jpg',1),('b.bin',2),('c.mp4',3)]:
            p=self.desk.root/name;p.write_bytes(b'exact bytes');os.utime(p,(age,age))
        (self.desk.root/'different.bin').write_bytes(b'other bytes')
        j=self.task('duplicates');j['result']=duplicates(self.desk,j);j['status']='done'
        self.assertEqual(len(j['result']['groups']),1)
        r=delete_duplicates(self.desk,{'scan':j['id'],'keep':'newest','confirm':True})
        self.assertEqual(r['removed'],2);self.assertTrue((self.desk.root/'c.mp4').exists());self.assertTrue((self.desk.root/'different.bin').exists())
    def test_duplicate_change_refuses_entire_delete(self):
        a=self.desk.root/'a';b=self.desk.root/'b';a.write_bytes(b'ab');b.write_bytes(b'ab')
        j=self.task('duplicates');j['result']=duplicates(self.desk,j);j['status']='done';b.write_bytes(b'cd')
        with self.assertRaises(ValueError):delete_duplicates(self.desk,{'scan':j['id'],'keep':'oldest','confirm':True})
        self.assertTrue(a.exists());self.assertTrue(b.exists())
    def test_upload_chunks_no_overwrite_and_offset(self):
        folder=self.desk.root/'Sub';folder.mkdir();(folder/'test.jpg').write_bytes(b'old')
        key=upload(self.desk,{'action':'start','folder':'Sub','name':'test.jpg','size':3})['id']
        with self.assertRaises(ValueError):upload(self.desk,{'action':'chunk','id':key,'offset':1,'data':base64.b64encode(b'new').decode()})
        upload(self.desk,{'action':'chunk','id':key,'offset':0,'data':base64.b64encode(b'new').decode()})
        r=upload(self.desk,{'action':'finish','id':key})
        self.assertEqual((self.desk.root/r['path']).read_bytes(),b'new');self.assertEqual((folder/'test.jpg').read_bytes(),b'old')
        self.assertFalse(self.desk.tasks.uploads)
    def test_bulk_delete_prevalidation_preserves_all(self):
        a=self.desk.root/'a';b=self.desk.root/'b';a.write_bytes(b'a');b.write_bytes(b'b')
        with self.assertRaises(ValueError):delete_batch(self.desk,{'files':[{'path':'a','stamp':stamp(a)},{'path':'b','stamp':'stale'}],'confirm':True})
        self.assertTrue(a.exists());self.assertTrue(b.exists())
    def test_torrent_usage_cursor_restart_and_upload(self):
        self.desk.record_torrent_usage({'downloadedBytes':100,'uploadedBytes':20})
        self.desk.record_torrent_usage({'downloadedBytes':150,'uploadedBytes':30})
        self.desk.record_torrent_usage({'downloadedBytes':150,'uploadedBytes':30})
        self.desk.record_torrent_usage({'downloadedBytes':7,'uploadedBytes':3})
        data=self.desk.usage_summary('day');self.assertEqual(data['total'],57);self.assertEqual(data['uploaded'],13)
    def test_remote_stream_rejects_local_and_alias_to_local(self):
        rem=self.desk.remotes
        def call(*args,**kwargs):
            if args[0]=='listremotes':return 'disk:\nwrapped:\n'
            if args[:2]==('config','dump'):return {'disk':{'type':'local'},'wrapped':{'type':'crypt','remote':'disk:folder'}}
            raise AssertionError(args)
        with patch.object(rem,'run',side_effect=call):
            for name in ('disk','wrapped'):
                with self.assertRaises(ValueError):rem.stream_args(name,'movie.mp4')
    def test_unknown_size_non_streaming_backend_refused(self):
        rem=self.desk.remotes
        def call(*args,**kwargs):
            if args[0]=='listremotes':return 'cloud:\n'
            if args[:2]==('config','dump'):return {'cloud':{'type':'drive'}}
            if args[:2]==('backend','features'):return {'Features':{'PutStream':False}}
            raise AssertionError(args)
        with patch.object(rem,'run',side_effect=call):
            with self.assertRaises(ValueError):rem.stream_args('cloud','v.mp4')
            self.assertIn('--size',rem.stream_args('cloud','v.mp4',size=123))
    def test_process_stop_cannot_address_arbitrary_pid(self):
        with self.assertRaises(ValueError):self.desk.tasks.cancel(str(os.getpid()))
    def test_remote_setup_survives_restart_and_delete_leaves_cloud_alone(self):
        if not __import__('shutil').which('rclone'):self.skipTest('rclone not installed')
        rem=self.desk.remotes
        result=rem.configure({'action':'start','name':'encrypted','provider':'crypt'})
        self.assertFalse(result['done']);self.assertEqual(result['option']['Name'],'remote')
        from remotes import Remotes
        restored=Remotes(self.desk)
        self.assertEqual(restored.configure({'action':'resume','name':'encrypted'})['option']['Name'],'remote')
        with self.assertRaises(ValueError):restored.target('encrypted','folder')
        restored.delete('encrypted',True)
        self.assertNotIn('encrypted',restored.names())

    def test_rclone_provider_schema_with_installed_binary(self):
        if not __import__('shutil').which('rclone'):self.skipTest('rclone not installed')
        self.assertIn('drive',{p['name'] for p in self.desk.remotes.providers()})
