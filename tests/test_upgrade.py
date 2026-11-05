"""Exercise deployment and rollback in temporary paths; no real service mutations."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import upgrade

class UpgradeTests(unittest.TestCase):
    def exercise(self, fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/'app';state=root/'state';config=root/'config';backups=root/'backups';source=root/'source'
            for p in (app,state,config,source):p.mkdir()
            (app/'static').mkdir();(app/'static/index.html').write_text('old UI')
            (app/'venv/bin').mkdir(parents=True);(app/'venv/bin/python').touch()
            (app/'server.py').write_text('old source')
            (state/'private.txt').write_text('private fixture state')
            (config/'config.json').write_text(json.dumps({'state':str(state)}))
            for name in upgrade.FILES:shutil.copyfile(upgrade.BASE/name,source/name)
            shutil.copytree(upgrade.BASE/'static',source/'static')
            def run(*args):
                if str(args[0])!='cp':return
                src=Path(str(args[2]).removesuffix('/.'));dst=Path(args[3])
                if str(args[2]).endswith('/.'):
                    for child in src.iterdir():
                        if child.is_dir():shutil.copytree(child,dst/child.name,dirs_exist_ok=True)
                        else:shutil.copy2(child,dst/child.name)
                elif src.is_dir():shutil.copytree(src,dst)
                else:shutil.copy2(src,dst)
            with patch.multiple(upgrade,BASE=source,APP=app,STATE=state,CONFIG=config,BACKUPS=backups),patch.object(upgrade,'run',side_effect=run),patch.object(upgrade,'stop'),patch.object(upgrade,'start'),patch.object(upgrade,'healthy',side_effect=RuntimeError('test failure') if fail else None),patch.object(upgrade,'validate_backup',side_effect=lambda p:p),patch('upgrade.os.geteuid',return_value=0),patch.object(sys,'argv',['upgrade.py']):
                if fail:
                    with self.assertRaises(RuntimeError):upgrade.main()
                    self.assertEqual((app/'server.py').read_text(),'old source')
                    self.assertEqual((app/'static/index.html').read_text(),'old UI')
                else:
                    upgrade.main();self.assertIn('class Desk',(app/'server.py').read_text())
                self.assertEqual((state/'private.txt').read_text(),'private fixture state')
                self.assertTrue((app/'venv/bin/python').exists())
                self.assertEqual(len(list(backups.iterdir())),1)
    def test_success_preserves_private_state_and_environment(self):self.exercise()
    def test_failed_health_check_restores_old_app_and_state(self):self.exercise(True)
