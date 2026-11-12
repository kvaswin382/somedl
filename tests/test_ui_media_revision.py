"""Reported v2 regressions and bounded background monitoring."""
import io
import json
import unittest
from unittest.mock import patch
import instagram_story
import engine_settings
import notifications
import maintenance
from health_alerts import Monitor
from server import DEFAULTS
from test_desk import Fixture

SHARE='https://www.instagram.com/s/aGlnaGxpZ2h0OjE3OTY1ODUwMDI4MTY4NjAx/'

class HighlightTests(unittest.TestCase):
    def test_exact_reported_share_is_a_canonical_highlight(self):
        from gallery_dl import extractor
        owner,url=instagram_story.target(SHARE,'highlights')
        self.assertEqual(owner,'')
        self.assertEqual(url,'https://www.instagram.com/stories/highlights/17965850028168601/')
        # Real installed gallery-dl construction: the reported error occurs here.
        ext=extractor.find(url)
        self.assertIsNotNone(ext)
        self.assertEqual(ext.subcategory,'highlights')
    def test_trailing_slash_and_query_do_not_enter_base64(self):
        expected=instagram_story.target(SHARE,'highlights')
        for url in (SHARE.rstrip('/'),SHARE+'?igsh=tracking'):
            self.assertEqual(instagram_story.target(url,'highlights'),expected)
    def test_bad_share_is_explicit_error(self):
        for token in ('aGlnaGxpZ2h0A','aGlnaGxpZ2h0Og','aGlnaGxpZ2h0OmFiYw'):
            with self.assertRaisesRegex(ValueError,'highlight'):
                instagram_story.target('https://www.instagram.com/s/'+token+'/','highlights')

class SettingsTests(unittest.TestCase):
    def test_concurrency_range_matches_ui_schema(self):
        self.assertEqual(engine_settings.DEFAULTS['simultaneous_downloads'],1)
        self.assertEqual(engine_settings.schema()['simultaneous_downloads'],{'min':1,'max':20,'step':1})
        for n in range(1,21):self.assertEqual(engine_settings.checked({'simultaneous_downloads':n})['simultaneous_downloads'],n)
        for n in (0,21,1.5,True):
            with self.assertRaises(ValueError):engine_settings.checked({'simultaneous_downloads':n})
    def test_alert_limits_are_nonzero_and_validated(self):
        for key in engine_settings.RANGES:
            if key.startswith('alert_'):
                self.assertGreater(engine_settings.schema()[key]['min'],0)
                with self.assertRaises(ValueError):engine_settings.checked({key:0})

class AlertsTests(unittest.TestCase):
    def setUp(self):
        self.monitor=Monitor();self.settings={**DEFAULTS,'telegram_bot_token':'123:fake'}
        self.readings={'cpu_percent':95,'memory':{'percent':20},'temperature_c':60,'system_disk':{'percent':20},'ssd':{'available':100*1024**3}}
    def test_sustained_cpu_and_per_metric_cooldown(self):
        self.assertEqual(self.monitor.sample(self.readings,self.settings,0),[])
        self.assertEqual(self.monitor.sample(self.readings,self.settings,59),[])
        alerts=self.monitor.sample(self.readings,self.settings,60)
        self.assertEqual(len(alerts),1);self.assertIn('CPU usage: 95.0 %',alerts[0])
        self.assertEqual(self.monitor.sample(self.readings,self.settings,100),[])
        self.readings['temperature_c']=85
        self.assertIn('CPU temperature',self.monitor.sample(self.readings,self.settings,101)[0])
        self.assertEqual(len(self.monitor.sample(self.readings,self.settings,960)),1)
    def test_transient_ram_and_missing_sensor_do_not_alert(self):
        self.readings.update(cpu_percent=None,temperature_c=None,memory={'percent':95})
        self.assertEqual(self.monitor.sample(self.readings,self.settings,0),[])
        self.readings['memory']['percent']=20
        self.assertEqual(self.monitor.sample(self.readings,self.settings,30),[])
        self.readings['memory']['percent']=95
        self.assertEqual(self.monitor.sample(self.readings,self.settings,70),[])
        self.assertIn('RAM usage',self.monitor.sample(self.readings,self.settings,130)[0])
    def test_ssd_alert_identifies_free_space(self):
        self.readings['ssd']['available']=2*1024**3
        self.assertIn('External SSD free space: 2.0 GiB',self.monitor.sample(self.readings,self.settings,0)[0])
    def test_actual_disk_snapshot_does_not_report_zero_free_space(self):
        from systemstats import disk_stats
        from collections import namedtuple
        usage=namedtuple('usage','total used free')(200*1024**3,100*1024**3,100*1024**3)
        with patch('systemstats.shutil.disk_usage',return_value=usage):self.readings['ssd']=disk_stats('/')
        self.assertEqual(self.monitor.sample(self.readings,self.settings,0),[])

    def test_disabled_monitor_is_silent(self):
        self.settings['health_alerts']=False;self.readings['temperature_c']=99
        self.assertEqual(self.monitor.sample(self.readings,self.settings,0),[])

class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.settings={'telegram_bot_token':'123:secret','telegram_chat_id':'-1001','telegram_admin_id':'42','telegram_pin_critical':True}
    def response(self,*args,**kwargs):return io.BytesIO(json.dumps({'ok':True,'result':{'message_id':7}}).encode())
    def test_general_errors_and_photos_only_go_to_group(self):
        for photo in (None,b'photo'):
            with patch('notifications.urllib.request.urlopen',side_effect=self.response) as request:
                self.assertTrue(notifications.send(self.settings,'Download failed https://example.com/?token=secret',photo)['ok'])
            self.assertEqual(request.call_count,1)
            data=request.call_args.args[0].data.decode()
            self.assertIn('-1001',data);self.assertNotIn('"42"',data);self.assertNotIn('?token',data)
    def test_critical_sends_to_both_and_pins_only_group(self):
        with patch('notifications.urllib.request.urlopen',side_effect=self.response) as request:
            result=notifications.send(self.settings,'<b>CPU usage: 95%</b>',critical=True)
        calls=[(c.args[0].full_url.rsplit('/',1)[1],json.loads(c.args[0].data)) for c in request.call_args_list]
        self.assertTrue(result['ok'])
        self.assertEqual([(method,data['chat_id']) for method,data in calls],[('sendMessage','-1001'),('pinChatMessage','-1001'),('sendMessage','42')])
        self.assertEqual(calls[0][1]['parse_mode'],'HTML')
    def test_pin_failure_does_not_lose_admin_alert_or_retry_delivery(self):
        def response(req,**kw):
            if req.full_url.endswith('/pinChatMessage'):raise PermissionError('private token secret')
            return self.response()
        with patch('notifications.urllib.request.urlopen',side_effect=response) as request:
            result=notifications.send(self.settings,'CPU usage',critical=True)
        self.assertTrue(result['ok']);self.assertEqual(request.call_count,3);self.assertTrue(result['warnings']);self.assertNotIn('secret',str(result))
    def test_admin_only_config_never_receives_general_logs(self):
        self.settings['telegram_chat_id']=''
        with patch('notifications.urllib.request.urlopen') as request:result=notifications.send(self.settings,'private error')
        request.assert_not_called();self.assertFalse(result['ok'])

class ReleaseTests(unittest.TestCase):
    def test_compare_stable_versions(self):
        for latest,current in [('1.10.0','1.9.9'),('2026.11.01','2026.10.26')]:self.assertTrue(maintenance.newer(latest,current))
        for latest,current in [('1.9.9','1.10.0'),('1.2.0','1.2'),('2.0rc1','1.0'),('2.0','Not installed'),(None,'1.0')]:self.assertFalse(maintenance.newer(latest,current))
    def test_gallery_distribution_name_is_normalized(self):
        from types import SimpleNamespace
        import tempfile,threading
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            desk=SimpleNamespace(get_setting=lambda *a:{})
            dist=SimpleNamespace(metadata={'Name':'gallery_dl'},version='1.32.19')
            with patch('maintenance.check_releases',return_value={}),patch('maintenance.importlib.metadata.distributions',return_value=[dist]),patch('maintenance.shutil.which',return_value=None),patch('maintenance.UPDATE_STATE',Path(tmp)):
                packages=maintenance.inventory(desk)['packages']
            gallery=[p for p in packages if p['name']=='gallery-dl']
            self.assertEqual(len(gallery),1);self.assertEqual(gallery[0]['version'],'1.32.19')

    def test_only_fixed_pypi_packages_queried(self):
        with patch('maintenance.urllib.request.urlopen',side_effect=lambda *a,**k:io.BytesIO(b'{"info":{"version":"2.0.0"}}')) as request:
            releases=maintenance.fetch_releases()
        self.assertEqual(set(releases),maintenance.UPDATES)
        self.assertEqual({c.args[0].full_url for c in request.call_args_list},{'https://pypi.org/pypi/'+p+'/json' for p in maintenance.UPDATES})

class DataTests(Fixture):
    def test_usage_hourly_and_daily_totals_agree(self):
        jid=self.create_job();self.desk.record_usage(jid,4096);self.desk.record_usage(jid,1024)
        day=self.desk.usage_summary('day')
        self.assertEqual(day['total'],5120);self.assertEqual(sum(p['bytes'] for p in day['series']),5120);self.assertEqual(len(day['series']),24)
        year=self.desk.usage_summary('year');self.assertEqual(len(year['series']),12);self.assertEqual(sum(p['bytes'] for p in year['series']),5120)
    def test_default_format_is_mp4_with_unrestricted_quality(self):
        jid=self.create_job();job=self.desk.job(jid)
        self.assertEqual(job['container'],'mp4');self.assertEqual(job['quality'],'best')

class BrowserSlotTests(unittest.TestCase):
    def test_browser_slot_covers_capture_and_transfer(self):
        import tempfile,threading,time,contextlib
        from pathlib import Path
        import download_engine
        operations=[];errors=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            specs=[]
            for n in range(2):
                dest=root/str(n);dest.mkdir()
                specs.append({'url':'https://example.com/watch/'+str(n),'dest':str(dest),'state':str(root),
                    'settings':{'refresh_attempts':0},'force':True,'container':'mp4','quality':'best','args':[]})
            def capture(url,jar,*args):
                operations.append('capture');time.sleep(.03)
                return {'kind':'video','url':'https://cdn.example.com/video.mp4','prefix':b''},'Video',jar
            def transfer(client,info,dest,*args,**kwargs):
                operations.append('transfer');time.sleep(.03)
                return dest/'video.mp4'
            def worker(spec):
                try:download_engine.run(spec)
                except Exception as exc:errors.append(str(exc))
            with patch('download_http.inspect',return_value={'kind':'page','html':''}),patch('browser_capture.capture',side_effect=capture),patch('download_http.transfer',side_effect=transfer),contextlib.redirect_stdout(io.StringIO()):
                threads=[threading.Thread(target=worker,args=(spec,),daemon=True) for spec in specs]
                for thread in threads:thread.start()
                for thread in threads:thread.join(3)
            self.assertFalse(any(t.is_alive() for t in threads));self.assertFalse(errors,errors)
            self.assertEqual(operations,['capture','transfer','capture','transfer'])

if __name__=='__main__':unittest.main()
