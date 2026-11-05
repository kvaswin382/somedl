import copy
import http.client
import json
import math
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_desk import Fixture
import test_revision as rev
from speedcheck import SpeedTest, parse_result, history_csv
from server import parse_range

REPORT={'type':'result','download':{'bandwidth':25000000,'bytes':250000000,'elapsed':10000},
        'upload':{'bandwidth':18750000,'bytes':187500000,'elapsed':10000},
        'ping':{'latency':5.25,'jitter':.7},'server':{'id':123,'name':'Test server','location':'Test city'},
        'isp':'Test ISP','packetLoss':0,'interface':{'externalIp':'private-test-value'},
        'result':{'url':'https://www.speedtest.net/result/c/test-fixture'}}

class SpeedTests(Fixture):
    def test_official_units_are_converted_once(self):
        result=parse_result(REPORT)
        self.assertEqual(result['download_mbps'],200)
        self.assertEqual(result['upload_mbps'],150)
        self.assertEqual(result['latency_ms'],5.25)
        self.assertNotIn('private-test-value',json.dumps(result))
        sample=copy.deepcopy(REPORT);sample['download']['bandwidth']=38404104
        self.assertEqual(parse_result(sample)['download_mbps'],307.23) # official CLI manual example

    def test_rejects_invalid_and_incomplete_results(self):
        for value in (None,-1,float('nan'),float('inf'),True,'200'):
            data=copy.deepcopy(REPORT);data['download']['bandwidth']=value
            with self.subTest(value=value),self.assertRaises(ValueError):parse_result(data)
        for key in ('bytes','elapsed'):
            data=copy.deepcopy(REPORT);data['download'][key]=0
            with self.assertRaises(ValueError):parse_result(data)
        with self.assertRaises(ValueError):parse_result({'type':'download','download':{'bandwidth':25000000}})

    def worker(self,script):
        path=self.base/'fake-speedtest'
        path.write_text('#!'+sys.executable+'\n'+script);path.chmod(0o755)
        self.desk.speedtest.binary=str(path)
        return self.desk.speedtest

    def test_final_report_persists_progress_does_not_become_result(self):
        worker=self.worker('import json\nprint(json.dumps({"type":"download","download":{"bandwidth":999999999,"progress":.5}}),flush=True)\nprint('+repr(json.dumps(REPORT))+',flush=True)\n')
        self.desk.set_setting('ookla_consent',True)
        worker.start();worker.thread.join(5)
        self.assertFalse(worker.thread.is_alive())
        self.assertEqual(worker.snapshot()['download_mbps'],200)
        self.assertEqual(worker.history()[0]['upload_mbps'],150)
        self.assertNotIn('live_mbps',worker.history()[0])
        self.assertEqual(worker.history()[0]['engine'],'ookla')

    def test_failed_progress_is_not_saved_as_completed_speed(self):
        worker=self.worker('import json\nprint(json.dumps({"type":"download","download":{"bandwidth":999999999,"progress":.1}}),flush=True)\nraise SystemExit(1)\n')
        self.desk.set_setting('ookla_consent',True)
        worker.start();worker.thread.join(5)
        self.assertEqual(worker.history()[0]['status'],'error')
        self.assertNotIn('download_mbps',worker.history()[0])

    def test_explicit_consent_and_missing_engine(self):
        worker=self.desk.speedtest;worker.binary=None
        with patch('speedcheck.cli_path',return_value=None),self.assertRaisesRegex(ValueError,'Install'):worker.start()
        worker.binary='/fixture-only'
        with patch('speedcheck.subprocess.Popen') as launch,self.assertRaisesRegex(ValueError,'accept'):worker.start()
        launch.assert_not_called()
        self.assertFalse(self.desk.get_setting('ookla_consent',False))

    def test_cancel_and_concurrent_test_guard(self):
        worker=self.worker('import time\ntime.sleep(30)\n')
        self.desk.set_setting('ookla_consent',True);worker.start()
        with self.assertRaisesRegex(ValueError,'already'):worker.start()
        worker.cancel();worker.thread.join(5)
        self.assertEqual(worker.snapshot()['status'],'cancelled')
        self.assertEqual(worker.history()[0]['status'],'cancelled')

    def test_legacy_reports_remain_labelled_and_csv_is_safe(self):
        self.desk.set_setting('speed_history',[{'finished':1,'download_mbps':350,'isp':'=CMD()'}])
        self.assertEqual(self.desk.speedtest.history()[0]['engine'],'legacy-http')
        csv=history_csv(self.desk.speedtest.history()).decode('utf-8-sig')
        self.assertIn("'=CMD()",csv);self.assertIn('legacy-http',csv)

class PreviewTests(Fixture):
    request=rev.NewHTTPTests.request
    login=rev.NewHTTPTests.login
    def setUp(self):
        Fixture.setUp(self)
        import threading
        from server import Server, Handler
        self.server=Server(('127.0.0.1',0),Handler);self.server.desk=self.desk
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.cookie='';self.csrf=''
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();Fixture.tearDown(self)

    def raw(self,path,method='GET',headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        conn.request(method,path,headers={'Cookie':self.cookie,**(headers or {})})
        r=conn.getresponse();result=(r.status,dict(r.getheaders()),r.read());conn.close();return result

    def test_preview_auth_types_inline_and_explicit_download(self):
        (self.desk.root/'photo.jpg').write_bytes(b'jpeg-fixture')
        self.assertEqual(self.raw('/api/preview?path=photo.jpg')[0],401)
        self.login()
        status,h,body=self.raw('/api/preview?path=photo.jpg')
        self.assertEqual((status,body),(200,b'jpeg-fixture'))
        self.assertEqual(h['Content-Type'],'image/jpeg');self.assertTrue(h['Content-Disposition'].startswith('inline'))
        status,h,_=self.raw('/api/file?path=photo.jpg')
        self.assertTrue(h['Content-Disposition'].startswith('attachment'))
        (self.desk.root/'bad.svg').write_text('<svg onload="alert(1)"/>')
        self.assertEqual(self.raw('/api/preview?path=bad.svg')[0],415)
        self.assertEqual(self.raw('/api/preview?path=../state')[0],400)
        (self.desk.root/'link.jpg').symlink_to(self.base/'outside')
        self.assertEqual(self.raw('/api/preview?path=link.jpg')[0],403)

    def test_video_ranges_head_and_folder_preview_kind(self):
        self.login();(self.desk.root/'movie.mp4').write_bytes(b'0123456789')
        for value,expected,cr in [('bytes=2-5',b'2345','bytes 2-5/10'),('bytes=7-',b'789','bytes 7-9/10'),('bytes=-3',b'789','bytes 7-9/10')]:
            status,h,body=self.raw('/api/preview?path=movie.mp4',headers={'Range':value})
            self.assertEqual((status,body),(206,expected));self.assertEqual(h['Content-Range'],cr)
            self.assertEqual(h['Content-Type'],'video/mp4')
        status,h,body=self.raw('/api/preview?path=movie.mp4','HEAD')
        self.assertEqual((status,h['Content-Length'],body),(200,'10',b''))
        status,h,body=self.raw('/api/preview?path=movie.mp4',headers={'Range':'bytes=99-'})
        self.assertEqual((status,h['Content-Range'],body),(416,'bytes */10',b''))
        status,h,body=self.raw('/api/folders')
        self.assertEqual(json.loads(body)['entries'][0]['preview'],'video')

    def test_reports_csv_requires_login(self):
        self.assertEqual(self.raw('/api/speedtest/history.csv')[0],401)
        self.login();status,h,body=self.raw('/api/speedtest/history.csv')
        self.assertEqual(status,200);self.assertIn('text/csv',h['Content-Type'])
        self.assertIn(b'download_mbps',body)

    def test_empty_and_invalid_ranges(self):
        self.assertEqual(parse_range(None,0),(0,-1))
        for value in ('bytes=0-1','bytes=-0','bytes=-','bytes=0-1,3-4'):
            with self.assertRaises(ValueError):parse_range(value,0)
