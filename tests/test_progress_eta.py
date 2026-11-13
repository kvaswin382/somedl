"""ETA must agree with current-file bytes and the rate shown in the queue."""
import contextlib,io,json,unittest
from unittest.mock import patch
from server import update_transfer_progress
from test_desk import Fixture
import download_http

class ProgressTests(unittest.TestCase):
    def test_ignores_inconsistent_engine_eta(self):
        live={};update_transfer_progress(live,{'downloaded_bytes':40*1024**2,'total_bytes':100*1024**2,'speed':2*1024**2,'eta':9000},10)
        self.assertEqual(live['eta'],30);self.assertEqual(live['progress'],40)
    def test_speed_change_recalculates_remaining_time(self):
        live={};update_transfer_progress(live,{'downloaded_bytes':100,'total_bytes':1100,'speed':100},10)
        self.assertEqual(live['eta'],10)
        update_transfer_progress(live,{'downloaded_bytes':200,'total_bytes':1100,'speed':10},11)
        self.assertEqual(live['eta'],90)
    def test_resumed_bytes_are_not_counted_as_new_speed(self):
        live={};update_transfer_progress(live,{'downloaded_bytes':900,'total_bytes':1100,'filename':'video'},10)
        self.assertIsNone(live['eta'])
        update_transfer_progress(live,{'downloaded_bytes':1000,'total_bytes':1100,'filename':'video'},12)
        self.assertEqual(live['speed'],50);self.assertEqual(live['eta'],2)
    def test_new_stream_and_counter_reset_do_not_reuse_rate(self):
        for change in ({'filename':'audio','downloaded_bytes':50},{'filename':'video','downloaded_bytes':50}):
            live={};update_transfer_progress(live,{'filename':'video','downloaded_bytes':500,'total_bytes':1100,'speed':100},10)
            update_transfer_progress(live,{'total_bytes':1000,**change},11)
            self.assertEqual(live['speed'],0);self.assertIsNone(live['eta'])
    def test_unknown_total_or_zero_rate_has_no_eta(self):
        for fields in ({'speed':0,'total_bytes':100},{'speed':10},{'speed':float('nan'),'total_bytes':100},{'speed':float('inf'),'total_bytes':100},{'speed':-1,'total_bytes':100},{'speed':'100','total_bytes':100}):
            live={};update_transfer_progress(live,{'downloaded_bytes':10,'eta':999,**fields},1)
            self.assertIsNone(live['eta'])
    def test_estimated_size_is_marked_and_not_treated_as_completed(self):
        live={};update_transfer_progress(live,{'downloaded_bytes':20,'total_bytes_estimate':100,'speed':10},1)
        self.assertEqual(live['eta'],8);self.assertTrue(live['eta_estimated'])
        update_transfer_progress(live,{'downloaded_bytes':120,'total_bytes_estimate':100,'speed':10},2)
        self.assertIsNone(live['eta']);self.assertEqual(live['transfer_state'],'downloading')
    def test_finished_clears_average_speed_and_eta(self):
        live={};update_transfer_progress(live,{'downloaded_bytes':100,'total_bytes':100,'speed':999,'eta':999,'status':'finished'},10)
        self.assertEqual(live['speed'],0);self.assertIsNone(live['eta']);self.assertEqual(live['transfer_state'],'finishing')

class WorkerProgressTests(Fixture):
    def feed(self,jid,*lines):
        self.desk.consume(io.BytesIO(('\n'.join(lines)+'\n').encode()),jid)
    def test_stale_rate_expires_in_snapshot_without_mutating_collector(self):
        jid=self.create_job();self.desk.update_job(jid,status='running')
        live=self.desk.live.setdefault(jid,{})
        update_transfer_progress(live,{'downloaded_bytes':20,'total_bytes':100,'speed':10},100)
        with patch('server.time.monotonic',return_value=111):
            shown=self.desk.snapshot()['jobs'][0]
        self.assertEqual(shown['speed'],0);self.assertIsNone(shown['eta']);self.assertEqual(shown['transfer_state'],'waiting')
        self.assertEqual(live['speed'],10)
        update_transfer_progress(live,{'downloaded_bytes':30,'total_bytes':100,'speed':5},112)
        with patch('server.time.monotonic',return_value=112):self.assertEqual(self.desk.live_progress(jid)['eta'],14)
    def test_finish_fallback_and_conversion_clear_stale_eta(self):
        jid=self.create_job()
        for line in ('ZEDENGINE browser','ZEDTITLE Next file','ZEDPROFILE Converting GIF to MP4 with one CPU thread.','ZEDPROFILE Refreshing the original page and expiring playback URL; keeping resumable partial files.'):
            self.feed(jid,'ZEDPROG '+json.dumps({'downloaded_bytes':20,'total_bytes':100,'speed':10,'eta':999}))
            self.assertEqual(self.desk.live[jid]['eta'],8)
            self.feed(jid,line);self.assertIsNone(self.desk.live[jid]['eta']);self.assertEqual(self.desk.live[jid]['speed'],0)
    def test_direct_transfer_recent_rate_and_final_event(self):
        # Real transfer loop, deterministic clock and response bytes: after a
        # slowdown the displayed rate must no longer be its lifetime average.
        payload=b'\xff\xd8\xff\xe0'+b'x'*(4*1024*1024)
        clock=[0.0]
        class Response(io.BytesIO):
            headers={'Content-Length':str(len(payload)),'Content-Type':'image/jpeg'}
            status=200;url='https://example.org/photo.jpg'
            def read(self,size=-1):
                clock[0]+=1 if self.tell()<2*1024*1024 else 4
                return super().read(size)
        class Client:
            def open(self,*a,**kw):return Response(payload)
        dest=self.base/'direct';dest.mkdir();output=io.StringIO()
        with patch('download_http.validate_url'),patch('download_http.time.monotonic',side_effect=lambda:clock[0]),contextlib.redirect_stdout(output):
            target=download_http.transfer(Client(),{'url':'https://example.org/photo.jpg'},dest,'Photo')
        self.assertEqual(target.read_bytes(),payload)
        events=[json.loads(line[8:]) for line in output.getvalue().splitlines() if line.startswith('ZEDPROG ')]
        self.assertEqual(events[-1]['status'],'finished');self.assertEqual(events[-1]['speed'],0)
        slow=[e for e in events if e.get('speed',0)>0 and e['downloaded_bytes']>3*1024*1024]
        self.assertTrue(slow);self.assertAlmostEqual(slow[0]['speed'],256*1024/4)
