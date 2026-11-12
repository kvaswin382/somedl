"""Updater recovery with private fixture paths; no packages or services are changed."""
import fcntl,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import engine_update

class EngineUpdateTests(unittest.TestCase):
    def test_failed_package_update_restores_engine_and_restarts_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);status=root/'status.json';calls=[]
            def run(args,**kwargs):
                calls.append(args)
                if '--upgrade' in args:raise subprocess.CalledProcessError(1,args)
                return subprocess.CompletedProcess(args,0)
            with patch.object(engine_update,'status_path',return_value=status),patch.object(engine_update,'UPDATE_LOCK',root/'lock'),patch.object(engine_update.importlib.metadata,'version',return_value='1.2.3'),patch.object(engine_update.subprocess,'run',side_effect=run):
                engine_update.update('yt-dlp')
            self.assertEqual(calls[0],['systemctl','stop','zedair'])
            self.assertEqual(calls[-1],['systemctl','start','zedair'])
            self.assertTrue(any('yt-dlp[default]==1.2.3' in c for c in calls))
            self.assertEqual(json.loads(status.read_text())['status'],'error')
    def test_timeout_recovery_restarts_service_and_updates_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);status=root/'status.json';status.write_text('{"status":"running"}')
            with patch.object(engine_update,'status_path',return_value=status),patch.object(engine_update,'UPDATE_LOCK',root/'lock'),patch.object(engine_update.os,'geteuid',return_value=0),patch.object(engine_update.subprocess,'run') as run:
                engine_update.recover('yt-dlp')
                run.assert_called_once_with(['systemctl','start','zedair'],check=True)
            self.assertEqual(json.loads(status.read_text())['status'],'error')
    def test_recovery_never_restarts_during_another_engine_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock=Path(tmp)/'lock'
            with lock.open('a') as guard:
                fcntl.flock(guard,fcntl.LOCK_EX)
                with patch.object(engine_update,'UPDATE_LOCK',lock),patch.object(engine_update.os,'geteuid',return_value=0),patch.object(engine_update.subprocess,'run') as run:
                    engine_update.recover('gallery-dl');run.assert_not_called()
