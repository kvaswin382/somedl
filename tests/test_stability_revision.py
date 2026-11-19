"""Queue limits, nonblocking maintenance and bounded file-browser metadata."""
import threading
import time
import subprocess
import sys
from unittest.mock import patch,Mock
from pathlib import Path
from test_desk import Fixture
import test_desk as baseline
from server import Server,Handler
from storage_tools import duplicates,delete_duplicates,stamp,scan_thumbnails,thumbnail_path,queue_thumbnails


class StabilityTests(Fixture):
    def setUp(self):
        super().setUp();self.desk.rpc.call=Mock(return_value={'torrents':[]})

    def scan(self,count=3):
        for i in range(count):(self.desk.root/f'copy-{i}').write_bytes(b'identical'*4096)
        key=self.desk.tasks.submit('duplicates',lambda job:None)['id']
        job=self.desk.tasks.jobs[key];job.update(result=duplicates(self.desk,job),status='done')
        self.desk.tasks.pending.clear()
        return job

    def test_scheduler_caps_preparing_workers_and_requeues_excess_on_reduction(self):
        with patch('server.validate_url'):
            ids=[self.create_job(f'https://example.com/{i}') for i in range(5)]
        self.desk.set_setting('simultaneous_downloads',3)
        try:
            with patch('server.threading.Thread') as thread:
                self.desk.media_tick();self.desk.media_tick()
                self.assertEqual(thread.call_count,3);self.assertEqual(len(self.desk.workers),3)
                for jid in ids[:3]:self.desk.update_job(jid,status='running')
                self.desk.set_setting('simultaneous_downloads',1);self.desk.enforce_media_limit()
                self.assertEqual([self.desk.job(j)['status'] for j in ids[:3]],['running','queued','queued'])
                self.desk.media_tick();self.assertEqual(thread.call_count,3)
                for jid in ids[1:3]:self.desk.workers.pop(jid)
                self.desk.media_tick();self.assertEqual(thread.call_count,3)
                self.desk.workers.pop(ids[0]);self.desk.update_job(ids[0],status='done')
                self.desk.media_tick();self.assertEqual(thread.call_count,4);self.assertEqual(len(self.desk.workers),1)
        finally:self.desk.workers.clear()

    def test_duplicate_progress_and_two_ownership_snapshots_for_whole_batch(self):
        scan=self.scan(8);job={'cancel':threading.Event()}
        result=delete_duplicates(self.desk,{'scan':scan['id'],'keep':'oldest','confirm':True},job)
        self.assertEqual(result['removed'],7);self.assertEqual(job['removed'],7)
        self.assertEqual(job['progress'],100);self.assertEqual(job['bytes'],job['total_bytes'])
        self.assertEqual(self.desk.rpc.call.call_count,2)
        self.assertEqual(len(list(self.desk.root.iterdir())),1)

    def test_reducing_limit_stops_real_worker_processes_and_preserves_queue(self):
        with patch('server.validate_url'):
            ids=[self.create_job(f'https://example.com/worker-{i}') for i in range(3)]
        popen=subprocess.Popen
        def process(args,**kwargs):
            return popen([sys.executable,'-u','-c',"import time; print('fixture transfer started',flush=True); time.sleep(30)"],**kwargs)
        def until(predicate):
            deadline=time.monotonic()+12
            while not predicate() and time.monotonic()<deadline:time.sleep(.03)
            self.assertTrue(predicate())
        self.desk.set_setting('simultaneous_downloads',3)
        with patch('server.validate_url'),patch('server.subprocess.Popen',side_effect=process):
            try:
                self.desk.media_tick();until(lambda:len(self.desk.processes)==3)
                children=list(self.desk.processes.values())
                self.desk.set_setting('simultaneous_downloads',1);self.desk.enforce_media_limit()
                until(lambda:len(self.desk.workers)==1)
                self.assertEqual(sum(p.poll() is None for p in children),1)
                self.assertEqual([self.desk.job(j)['status'] for j in ids],['running','queued','queued'])
                self.assertTrue(all(not self.desk.job(j)['last_error'] for j in ids))
            finally:
                for jid in ids:self.desk.job_action(jid,'pause')
                until(lambda:not self.desk.workers)

    def test_cancel_during_duplicate_verification_deletes_nothing(self):
        scan=self.scan();job={'cancel':threading.Event()};check=self.desk.tasks.check
        def stop(j):
            if j.get('bytes',0)>0:j['cancel'].set()
            check(j)
        with patch.object(self.desk.tasks,'check',side_effect=stop),self.assertRaises(InterruptedError):
            delete_duplicates(self.desk,{'scan':scan['id'],'keep':'oldest','confirm':True},job)
        self.assertEqual(len(list(self.desk.root.iterdir())),3)
        self.assertEqual(job['removed'],0);self.assertGreater(job['bytes'],0)

    def test_failed_previews_do_not_starve_later_videos_or_rewrite_old_files(self):
        files=[]
        for i in range(21):
            p=self.desk.root/f'{i:02}.mp4';p.write_bytes(b'fixture');files.append(p)
        job={'cancel':threading.Event()}
        calls=[]
        def make(d,j,p,embed=True):
            calls.append((p.name,embed))
            if p!=files[-1]:raise ValueError('Invalid video')
            out=thumbnail_path(d,p);out.parent.mkdir(exist_ok=True);out.write_bytes(b'preview')
        with patch('storage_tools.thumbnail',side_effect=make),patch('web_session.available_memory',return_value=1024**3):
            scan_thumbnails(self.desk,job,paths=files)
            scan_thumbnails(self.desk,job,paths=files)
        self.assertEqual(calls[-1],('20.mp4',False));self.assertEqual(len(calls),21)
        self.assertTrue(thumbnail_path(self.desk,files[-1]).is_file())

    def test_requested_preview_work_is_coalesced(self):
        files=[self.desk.root/f'{i}.mp4' for i in range(4)]
        queue_thumbnails(self.desk,files);queue_thumbnails(self.desk,files,embed=True)
        self.assertEqual(len(self.desk.tasks.pending),1)
        self.assertEqual(len(self.desk.thumbnail_pending),4)
        self.assertTrue(all(self.desk.thumbnail_pending.values()))

    def test_idle_torrent_poll_does_not_write_database(self):
        stats={'downloadedBytes':100,'uploadedBytes':30}
        self.desk.record_torrent_usage(stats)
        with patch.object(self.desk,'flush_usage') as flush:
            self.desk.record_torrent_usage({**stats,'secondsActive':999})
            flush.assert_not_called()


class StabilityHTTP(Fixture):
    request=baseline.HTTPTests.request
    login=baseline.HTTPTests.login
    def setUp(self):
        super().setUp();self.desk.rpc.call=Mock(return_value={'torrents':[]})
        self.server=Server(('127.0.0.1',0),Handler);self.server.desk=self.desk
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.cookie='';self.csrf='';self.login()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()

    def test_concurrency_save_does_not_wait_for_torrent_rpc(self):
        with patch.object(self.desk.rpc,'call',side_effect=AssertionError('Unrelated RPC')):
            status,_,_=self.request('/api/settings',{'simultaneous_downloads':2})
        self.assertEqual(status,200);self.assertEqual(self.desk.get_setting('simultaneous_downloads'),2)

    def test_folder_page_checks_only_visible_thumbnails_and_poll_does_not_rescan(self):
        for i in range(250):(self.desk.root/f'{i:03}.mp4').write_bytes(b'fixture')
        with patch('storage_tools.thumbnail_status',return_value='pending') as check,patch('storage_tools.queue_thumbnails') as enqueue:
            status,r,_=self.request('/api/folders?limit=24&sort=name&order=asc')
            self.assertEqual(status,200);self.assertEqual(r['total'],250)
            self.assertEqual(check.call_count,24);self.assertEqual(len(enqueue.call_args.args[1]),24)
        with patch('server.os.scandir',side_effect=AssertionError('Unexpected directory rescan')),patch('storage_tools.queue_thumbnails'):
            status,r,_=self.request('/api/thumbnails?name=000.mp4')
        self.assertEqual(status,200);self.assertEqual(r['entries'][0]['thumbnail_state'],'pending')

    def test_duplicate_delete_returns_task_and_keeps_read_api_responsive(self):
        for name in ('one','two'):(self.desk.root/name).write_bytes(b'equal')
        scan_id=self.desk.tasks.submit('duplicates',lambda job:None)['id'];scan=self.desk.tasks.jobs[scan_id]
        scan.update(result=duplicates(self.desk,scan),status='done');self.desk.tasks.pending.clear()
        payload={'scan':scan_id,'keep':'oldest','confirm':True}
        status,r,_=self.request('/api/duplicates/delete',payload)
        self.assertEqual(status,200);self.assertIn('id',r)
        self.assertTrue((self.desk.root/'two').exists())
        self.assertEqual(self.request('/api/duplicates/delete',payload)[1]['id'],r['id'])
        entered=threading.Event();release=threading.Event()
        from storage_tools import protection
        def slow(d):entered.set();release.wait(5);return protection(d)
        worker=threading.Thread(target=self.desk.tasks.loop,daemon=True)
        try:
            with patch('storage_tools.protection',side_effect=slow):
                worker.start();self.assertTrue(entered.wait(3))
                self.assertEqual(self.request('/api/folders')[0],200)
                jobs=self.request('/api/tasks')[1]['tasks'];task=next(j for j in jobs if j['id']==r['id'])
                self.assertEqual(task['status'],'running');self.assertEqual(task['removed'],0)
                release.set()
                deadline=time.monotonic()+5
                while self.desk.tasks.jobs[r['id']]['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(self.desk.tasks.jobs[r['id']]['status'],'done')
            self.assertEqual(self.desk.tasks.jobs[r['id']]['progress'],100)
        finally:release.set();self.desk.stop.set();worker.join(3)
