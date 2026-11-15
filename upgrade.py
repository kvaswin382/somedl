#!/usr/bin/env python3
"""Install a checked-out source revision without touching the SSD or fstab."""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BASE=Path(__file__).resolve().parent
APP=Path('/opt/zedair')
STATE=Path('/var/lib/zedair')
CONFIG=Path('/etc/zedair')
BACKUPS=Path('/var/backups/zedair')
FILES=('download_http.py','engine_settings.py','torrent_engine.py','torrent_migrate.py','torrent_service.py','web_session.py','notifications.py','health_alerts.py','browser_capture.py','download_engine.py','instagram_story.py','maintenance.py','engine_update.py','server.py','media.py','platforms.py','publishing.py','speedcheck.py','systemstats.py', 'profiles.py', 'facebook_profile.py', 'instagram_profile.py', 'browser_video.py', 'page_media.py', 'fileops.py', 'setup-local.py', 'setup-local.sh', 'install-facebook.sh', 'install-browser.sh','install-speedtest.py','install-speedtest.sh','README.md','TEST-REPORT.md','LICENSE','requirements.txt','update.sh','upgrade.sh','upgrade.py')

def run(*args):
    subprocess.run([str(x) for x in args],check=True)

ENGINE_UNITS=('zedair-qbittorrent','zedair-transmission')

def engine_unit():
    # The installed application decides which torrent engine is current.
    return ENGINE_UNITS[0 if (APP/'torrent_engine.py').exists() else 1]

def stop():
    run('systemctl','stop','zedair')
    for unit in ENGINE_UNITS:  # one of them may not exist; that is fine
        subprocess.run(['systemctl','stop',unit],check=False)

def start():
    # Web UI still starts if the SSD is absent and the torrent engine cannot start.
    subprocess.run(['systemctl','start',engine_unit()],check=False)
    run('systemctl','start','zedair')

def switch_engine(config):
    """Make systemd match the installed application (also used by rollback)."""
    import torrent_service
    if (APP/'torrent_engine.py').exists():
        torrent_service.install_services(config,APP,STATE,CONFIG/'config.json')
    else:
        torrent_service.select_engine('transmission')

def backup():
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    destination=BACKUPS/stamp
    destination.mkdir(parents=True,mode=0o700)
    (destination/'app').mkdir()
    for name in FILES:
        if (APP/name).exists():run('cp','-a',APP/name,destination/'app'/name)
    run('cp','-a',APP/'static',destination/'app'/'static')
    run('cp','-a',STATE,destination/'state')
    run('cp','-a',CONFIG,destination/'config')
    (destination/'backup.json').write_text(json.dumps({'format':1,'created':stamp}))
    print('Backup saved: '+str(destination),flush=True)
    return destination

def validate_backup(destination):
    # Backups are private, root-owned directories created by this updater.
    destination=destination.resolve()
    if destination.parent!=BACKUPS.resolve() or destination.is_symlink() or destination.stat().st_uid!=0:
        raise ValueError('Use a root-owned backup directly under /var/backups/zedair')
    if json.loads((destination/'backup.json').read_text()).get('format')!=1:
        raise ValueError('Unsupported backup format')
    return destination

def restore(destination):
    destination=validate_backup(destination)
    for name in FILES:
        (APP/name).unlink(missing_ok=True)
    shutil.rmtree(APP/'static')
    run('cp','-a',str(destination/'app')+'/.',APP)
    # Remove current state only after backup validation. Caller saves a fresh backup first.
    shutil.rmtree(STATE)
    shutil.rmtree(CONFIG)
    run('cp','-a',destination/'state',STATE)
    run('cp','-a',destination/'config',CONFIG)

def healthy():
    config=json.loads((CONFIG/'config.json').read_text())
    # A TLS-configured host may use a private CA. Check systemd instead of bypassing TLS checks.
    if config.get('tls_cert'):
        time.sleep(2)
        run('systemctl','is-active','--quiet','zedair')
        return
    host=config.get('host','0.0.0.0')
    if host in ('0.0.0.0','::'):host='127.0.0.1'
    url=f"http://{host}:{config.get('port',8787)}/api/auth"
    for _ in range(15):
        try:
            with urllib.request.urlopen(url,timeout=2) as response:
                if 'authenticated' in json.load(response):return
        except (OSError,ValueError):pass
        time.sleep(1)
    raise RuntimeError('Updated web app did not pass its local health check')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback',type=Path,help='Restore an updater backup, saving the current installation first')
    args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('Run: sudo bash upgrade.sh')
    if not (CONFIG/'config.json').is_file() or not (APP/'venv/bin/python').exists():
        raise SystemExit('No existing installation found. Use sudo bash install.sh for first installation.')
    config=json.loads((CONFIG/'config.json').read_text())
    if Path(config['state'])!=STATE:
        raise SystemExit('Custom state path detected. This updater supports the installer defaults only.')
    if not args.rollback:
        if BASE==APP:raise SystemExit('Run upgrade.sh from your Git checkout or extracted new package, not /opt/zedair.')
        for name in FILES:
            if not (BASE/name).is_file():raise SystemExit('Missing package file: '+name)
        with tempfile.TemporaryDirectory() as temp:
            for name in ('download_http.py','engine_settings.py','web_session.py','notifications.py','health_alerts.py','browser_capture.py','download_engine.py','instagram_story.py','maintenance.py','engine_update.py','server.py','media.py','platforms.py','publishing.py','speedcheck.py','systemstats.py','profiles.py','facebook_profile.py', 'instagram_profile.py', 'browser_video.py', 'page_media.py', 'fileops.py','setup-local.py','install-speedtest.py','upgrade.py','torrent_engine.py','torrent_migrate.py','torrent_service.py'):
                import py_compile
                py_compile.compile(str(BASE/name),cfile=str(Path(temp)/(name+'c')),doraise=True)
        run(APP/'venv/bin/python','-c','import yt_dlp, gallery_dl')
    elif not (args.rollback/'backup.json').is_file():
        raise SystemExit('Backup not found')
    if not args.rollback and APP==Path('/opt/zedair'):
        # Fetch the torrent engine first: a failure here changes nothing.
        run('apt-get','install','-y','qbittorrent-nox')
    required=sum(p.stat().st_size for p in STATE.rglob('*') if p.is_file())+256*1024**2
    if shutil.disk_usage('/var').free<required:
        raise SystemExit('Not enough internal disk space for a safe backup. Free space before updating.')
    os.umask(0o077)
    BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o700)
    stop()
    saved=None
    try:
        saved=backup()
        if args.rollback:
            restore(args.rollback)
        else:
            for name in FILES:
                shutil.copyfile(BASE/name,APP/name)
                (APP/name).chmod(0o644)
            shutil.rmtree(APP/'static')
            shutil.copytree(BASE/'static',APP/'static')
            for item in (APP/'static').rglob('*'):
                item.chmod(0o755 if item.is_dir() else 0o644)
            (APP/'static').chmod(0o755)
        if APP==Path('/opt/zedair'):
            from maintenance import install_update_service
            install_update_service()
            switch_engine(json.loads((CONFIG/'config.json').read_text()))
        start()
        healthy()
    except Exception:
        if saved:
            stop()
            restore(saved)
            if APP==Path('/opt/zedair'):switch_engine(json.loads((CONFIG/'config.json').read_text()))
            start()
            print('Update failed. Previous application and private state restored.',file=sys.stderr)
        else:
            start()
        raise
    print('Zedair update complete. Refresh the browser (Ctrl+F5).')
    print('SSD files, mount configuration and Python engine versions were not changed.')
    print('Keep the backup until you have checked the app. Backups contain private login cookies.')

if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('Upgrade stopped: '+str(exc))
