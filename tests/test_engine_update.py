"""Read-only engine commands and removal of the privileged GUI updater."""
import subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import maintenance

class EngineCommandTests(unittest.TestCase):
    def inventory(self,versions,latest):
        distributions=[SimpleNamespace(metadata={'Name':k},version=v) for k,v in versions.items()]
        desk=SimpleNamespace(get_setting=lambda *a:{})
        with patch('maintenance.importlib.metadata.distributions',return_value=distributions),patch('maintenance.shutil.which',return_value=None),patch('maintenance.check_releases',return_value={'packages':latest}),patch('maintenance.subprocess.run') as run:
            result=maintenance.inventory(desk);run.assert_not_called();return result
    def test_only_newer_engines_offer_commands(self):
        r=self.inventory(dict.fromkeys(maintenance.UPDATES,'2.0'),{'yt-dlp':{'latest_version':'3.0'},'gallery-dl':{'latest_version':'2.0'},'playwright':{'latest_version':'1.0'}})
        self.assertEqual([p['name'] for p in r['packages'] if p['command']],['yt-dlp']);self.assertEqual(r['all_command'],'')
    def test_all_command_requires_all_three_newer(self):
        latest={n:{'latest_version':'3.0'} for n in maintenance.UPDATES}
        r=self.inventory(dict.fromkeys(maintenance.UPDATES,'2.0'),latest)
        self.assertIn("'yt-dlp[default]' gallery-dl playwright",r['all_command']);self.assertIn('install-browser.sh --installed',r['all_command'])
        for missing in maintenance.UPDATES:
            result=self.inventory({n:'2.0' for n in maintenance.UPDATES if n!=missing},latest)
            self.assertEqual(result['all_command'],'')
    def test_missing_browser_gets_install_commands_even_offline(self):
        r=self.inventory({'yt-dlp':'2.0','gallery-dl':'2.0'},{'playwright':{'check_error':True}})
        p=next(p for p in r['packages'] if p['name']=='playwright')
        self.assertEqual(p['version'],'Not installed');self.assertFalse(p['has_update'])
        self.assertIn('pip install --no-cache-dir --upgrade playwright &&\n',p['command']);self.assertIn('install-browser.sh --installed',p['command'])
    def test_unavailable_or_invalid_release_never_offers_update(self):
        r=self.inventory(dict.fromkeys(maintenance.UPDATES,'2.0'),{'yt-dlp':{'check_error':True},'gallery-dl':{'latest_version':'$(reboot)'},'playwright':{'latest_version':'3rc1'}})
        self.assertFalse(any(p['command'] for p in r['packages']));self.assertEqual(r['all_command'],'')
    def test_legacy_shell_updater_is_inert(self):
        script=Path(__file__).resolve().parents[1]/'update.sh'
        with tempfile.TemporaryDirectory() as tmp:
            fake=Path(tmp)/'sudo';fake.write_text('#!/bin/sh\nexit 99\n');fake.chmod(0o755)
            result=subprocess.run(['/bin/bash',str(script)],capture_output=True,text=True,env={'PATH':tmp})
        self.assertEqual(result.returncode,1);self.assertIn('removed',result.stdout)

class RetireUpdaterTests(unittest.TestCase):
    def exercise(self,active=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rule=root/'rule';unit=root/'updater.service';script=root/'engine_update.py';state=root/'state';state.mkdir()
            for p in (rule,unit,script,state/'integration.json'):p.write_text('legacy')
            (state/'update-playwright.json').write_text('keep diagnostics')
            def run(args,**kwargs):return subprocess.CompletedProcess(args,0 if active else 3,stdout='active\n' if active else 'inactive\n')
            with patch.multiple(maintenance,UPDATE_RULE=rule,UPDATE_UNIT=unit,LEGACY_SCRIPT=script,LEGACY_STATE=state),patch('maintenance.os.geteuid',return_value=0),patch('maintenance.subprocess.run',side_effect=run) as command:
                if active:
                    with self.assertRaisesRegex(ValueError,'still be running'):maintenance.retire_update_service()
                    self.assertTrue(script.exists());self.assertFalse(unit.is_symlink())
                else:
                    maintenance.retire_update_service();self.assertEqual(unit.readlink(),Path('/dev/null'));self.assertFalse(script.exists());self.assertFalse((state/'integration.json').exists())
                self.assertFalse(rule.exists());self.assertTrue((state/'update-playwright.json').exists())
                self.assertFalse(any(any(x in c.args[0] for x in ('stop','start','pip','kill')) for c in command.call_args_list))
    def test_retirement_removes_permission_masks_service_keeps_logs(self):self.exercise()
    def test_running_legacy_update_is_not_interrupted(self):self.exercise(True)
    def test_web_account_cannot_change_system_integration(self):
        with patch('maintenance.os.geteuid',return_value=1000),patch('maintenance.subprocess.run') as command:
            with self.assertRaises(ValueError):maintenance.retire_update_service()
            command.assert_not_called()
