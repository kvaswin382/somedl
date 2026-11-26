"""Verify retired updater masking on isolated real systemd units; never run pip."""
import os,subprocess,sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def worker():
    import maintenance
    original=subprocess.run;prefix='zedair-retired-test-'+str(os.getpid())
    unit=Path('/run/systemd/system')/(prefix+'@.service')
    def command(*args,check=True):return original(['systemctl',*args],capture_output=True,text=True,check=check)
    def routed(args,**kwargs):
        return original([v.replace('zedair-update@',prefix+'@') for v in args],**kwargs)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);rule=root/'updater.rules';script=root/'engine_update.py';marker=root/'integration.json'
        for path in (rule,script,marker):path.write_text('fixture')
        unit.write_text('[Service]\nType=simple\nExecStart=/usr/bin/sleep 300\n')
        try:
            command('daemon-reload');command('start',prefix+'@playwright.service')
            with patch.multiple(maintenance,UPDATE_RULE=rule,UPDATE_UNIT=unit,LEGACY_SCRIPT=script,LEGACY_STATE=root),patch('maintenance.subprocess.run',side_effect=routed):
                try:maintenance.retire_update_service()
                except ValueError as exc:assert 'still be running' in str(exc)
                else:raise AssertionError('Running updater must prevent deployment')
                assert not rule.exists() and script.exists()
                command('is-active','--quiet',prefix+'@playwright.service')
                command('stop',prefix+'@playwright.service')
                maintenance.retire_update_service()
                assert unit.is_symlink() and unit.readlink()==Path('/dev/null')
                assert not script.exists() and not marker.exists()
                for engine in sorted(maintenance.UPDATES):
                    failed=command('start',prefix+'@'+engine+'.service',check=False)
                    assert failed.returncode!=0 and 'masked' in failed.stderr,failed
            print('PASS: real systemd refuses all retired updater starts; active legacy work is not interrupted and permission is revoked.')
        finally:
            for engine in maintenance.UPDATES:command('stop',prefix+'@'+engine+'.service',check=False)
            unit.unlink(missing_ok=True);command('daemon-reload')
            for engine in maintenance.UPDATES:command('reset-failed',prefix+'@'+engine+'.service',check=False)

if '--worker' in sys.argv:worker()
elif Path('/run/systemd/system').is_dir() and os.environ.get('GITHUB_ACTIONS')=='true':
    subprocess.run(['sudo',sys.executable,str(Path(__file__).resolve()),'--worker'],check=True,timeout=90)
else:
    if os.environ.get('ZEDAIR_REQUIRE_BROWSER')=='1':raise RuntimeError('CI must verify updater retirement under real systemd')
    print('SKIP: systemd retirement fixture requires GitHub Actions; no local service manager.')
