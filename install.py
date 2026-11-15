#!/usr/bin/env python3
"""Interactive Ubuntu installer. Never formats or partitions a drive."""
import datetime
import hashlib
import json
import os
import platform
import pwd
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import torrent_service

BASE = Path(__file__).resolve().parent
APP = Path('/opt/zedair')
STATE = Path('/var/lib/zedair')
MOUNT = Path('/srv/zedair-ssd')
CONFIG = Path('/etc/zedair/config.json')


def run(*args, capture=False):
    print('+', ' '.join(str(a) for a in args[:3]), flush=True)
    return subprocess.run([str(a) for a in args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def save(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.zedair-new')
    temp.write_text(content)
    temp.chmod(mode)
    os.replace(temp, path)


def walk_disks(items):
    for item in items:
        yield item
        yield from walk_disks(item.get('children', []))


def setup_deno():
    target = APP / 'bin' / 'deno'
    if target.exists():
        run(target, '--version')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print('Downloading the official Deno runtime for YouTube challenge support…')
    url = 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip'
    with tempfile.TemporaryDirectory(prefix='zedair-install-') as temp:
        archive = Path(temp)/'deno.zip'
        with urllib.request.urlopen(url, timeout=120) as response, archive.open('wb') as out:
            shutil.copyfileobj(response, out, 1024*1024)
        with zipfile.ZipFile(archive) as package:
            # Do not extract arbitrary archive paths.
            with package.open('deno') as source, target.open('wb') as out:
                shutil.copyfileobj(source, out, 1024*1024)
        target.chmod(0o755)
    run(target, '--version')


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run: sudo bash install.sh')
    if platform.machine() != 'x86_64':
        raise SystemExit('This package targets Ubuntu x86_64, matching your Zedair.')
    release = Path('/etc/os-release').read_text()
    if 'ID=ubuntu' not in release or sys.version_info < (3, 11):
        raise SystemExit('Use Ubuntu 24.04 or newer with Python 3.11+.')
    print('\nZEDAIR DOWNLOAD DESK\nNo formatting. No repartitioning. Existing files stay in place.\n')
    previous = json.loads(CONFIG.read_text()) if CONFIG.exists() else None
    device = None
    if not previous:
        disks = json.loads(run('lsblk', '-J', '-p', '-o', 'NAME,SIZE,FSTYPE,MOUNTPOINTS,UUID', capture=True))['blockdevices']
        candidates = [d for d in walk_disks(disks) if d.get('fstype') == 'exfat' and d.get('uuid')]
        if not candidates:
            raise SystemExit('No exFAT SSD found. Connect your SSD, then run this installer again.')
        for i, disk in enumerate(candidates, 1):
            print(f"{i}. {disk['name']}  {disk['size']}  exFAT  UUID={disk['uuid']}  mounted={disk.get('mountpoints')}")
        choice = input('\nSSD number [1]: ').strip() or '1'
        if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
            raise SystemExit('Invalid choice. Nothing was changed.')
        disk = candidates[int(choice)-1]
        device, uuid = disk['name'], disk['uuid']
        if not re.fullmatch(r'[A-Fa-f0-9-]+', uuid):
            raise SystemExit('Unexpected drive UUID')
        mounted = [m for m in disk.get('mountpoints', []) if m]
        if mounted:
            raise SystemExit('This drive is already mounted at '+', '.join(mounted)+'. Safely unmount it first, then rerun. No mount configuration was changed.')
        fstab = Path('/etc/fstab').read_text()
        for line in fstab.splitlines():
            fields = line.split()
            if fields and not fields[0].startswith('#') and (uuid.lower() in fields[0].lower() or (len(fields)>1 and fields[1]==str(MOUNT))):
                raise SystemExit('An existing fstab entry uses this SSD or mount point. Review it before installing; it has not been changed.')
        if MOUNT.exists() and any(MOUNT.iterdir()):
            raise SystemExit(f'{MOUNT} is not empty. Choose a clean mount location before continuing.')
        print(f'\nWill mount {device} by UUID at {MOUNT}, and use ONLY its Zedair folder.')
        print('Installs Python download tools, FFmpeg, Deno and a private qBittorrent-nox service. Web port: 8787.')
        if input('Type INSTALL to continue: ').strip() != 'INSTALL':
            raise SystemExit('Cancelled. Nothing was changed.')
    else:
        uuid = previous['ssd_uuid']
        print('Existing Zedair installation found. Updating application files; keeping account, queue and configuration.')
    # Avoid adding unbounded pip caches to the 32 GB eMMC.
    if shutil.disk_usage('/').free < 2*1024**3:
        raise SystemExit('Keep at least 2 GiB free on the internal drive before installation.')
    run('apt-get', 'update')
    run('apt-get', 'install', '-y', 'python3-venv', 'ffmpeg', 'qbittorrent-nox', 'exfatprogs', 'ca-certificates', 'polkitd')
    try:
        user = pwd.getpwnam('zedair')
    except KeyError:
        run('useradd', '--system', '--user-group', '--home-dir', STATE, '--shell', '/usr/sbin/nologin', 'zedair')
        user = pwd.getpwnam('zedair')
    STATE.mkdir(exist_ok=True, mode=0o700)
    os.chown(STATE, user.pw_uid, user.pw_gid)
    STATE.chmod(0o700)
    APP.mkdir(parents=True, exist_ok=True)
    CONFIG.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(CONFIG.parent, 0, user.pw_gid)
    setup_key = None
    if previous:
        config = previous
    else:
        setup_key = secrets.token_urlsafe(24)
        config = {'host': '0.0.0.0', 'port': 8787, 'state': str(STATE), 'mount': str(MOUNT),
                  'require_mount': True, 'ssd_uuid': uuid, 'setup_key_hash': hashlib.sha256(setup_key.encode()).hexdigest(),
                  'rpc_url': 'http://127.0.0.1:9092', 'rpc_user': 'zedair',
                  'rpc_password': secrets.token_urlsafe(36)}
        save(CONFIG.parent/'setup-key', setup_key+'\n', 0o600)
    if not previous:
        MOUNT.mkdir(parents=True, exist_ok=True, mode=0o555)
        MOUNT.chmod(0o555)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        shutil.copy2('/etc/fstab', '/etc/fstab.zedair-backup-'+stamp)
        fstab = Path('/etc/fstab').read_text()
        entry = f'UUID={uuid} {MOUNT} exfat defaults,nofail,x-systemd.device-timeout=10,uid={user.pw_uid},gid={user.pw_gid},fmask=0177,dmask=0077,noexec,nodev,nosuid 0 0'
        save(Path('/etc/fstab'), fstab.rstrip()+'\n\n# Zedair external SSD — existing exFAT; never format\n'+entry+'\n')
        save(CONFIG, json.dumps(config, indent=2)+'\n', 0o640)
        os.chown(CONFIG, 0, user.pw_gid)
        run('systemctl', 'daemon-reload')
        run('mount', MOUNT)
    elif not os.path.ismount(MOUNT):
        run('mount', MOUNT)
    actual = os.stat('/dev/disk/by-uuid/'+uuid).st_rdev
    if not os.path.ismount(MOUNT) or os.stat(MOUNT).st_dev != actual:
        raise SystemExit('SSD mount verification failed. Services were not installed.')
    root = MOUNT/'Zedair'
    if root.is_symlink():
        raise SystemExit('Zedair must be a real directory, not a link.')
    root.mkdir(exist_ok=True)
    for folder in ('YouTube', 'Instagram', 'X', 'Threads', 'Torrents'):
        (root/folder).mkdir(exist_ok=True)
    if previous and Path('/etc/systemd/system/zedair.service').exists():
        run('systemctl', 'stop', 'zedair.service')
    for name in ('download_http.py','engine_settings.py','torrent_engine.py','torrent_migrate.py','torrent_service.py','web_session.py','notifications.py','health_alerts.py','browser_capture.py','download_engine.py','instagram_story.py','maintenance.py','engine_update.py','server.py', 'media.py', 'platforms.py', 'publishing.py', 'speedcheck.py','systemstats.py', 'profiles.py', 'facebook_profile.py', 'instagram_profile.py', 'browser_video.py', 'page_media.py', 'fileops.py', 'setup-local.py', 'setup-local.sh', 'install-facebook.sh', 'install-browser.sh', 'install-speedtest.py', 'install-speedtest.sh', 'README.md', 'TEST-REPORT.md', 'LICENSE', 'requirements.txt', 'update.sh', 'upgrade.sh', 'upgrade.py'):
        shutil.copy2(BASE/name, APP/name)
    shutil.copytree(BASE/'static', APP/'static', dirs_exist_ok=True)
    run('python3', '-m', 'venv', APP/'venv')
    run(APP/'venv/bin/python', '-m', 'pip', 'install', '--no-cache-dir', '-r', APP/'requirements.txt')
    try:
        setup_deno()
    except Exception as exc:
        raise SystemExit('Deno installation failed: '+str(exc)+'. Rerun install.sh after fixing connectivity/runtime compatibility. SSD files are safe.')
    save(CONFIG, json.dumps(config, indent=2)+'\n', 0o640)
    os.chown(CONFIG, 0, user.pw_gid)
    from maintenance import install_update_service
    install_update_service()
    torrent_service.install_services(config, APP, STATE, CONFIG)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', torrent_service.QBT_UNIT, 'zedair.service')
    run('systemctl', 'restart', 'zedair.service')
    run('systemctl', 'is-active', 'zedair.service', torrent_service.QBT_UNIT)
    ips = run('hostname', '-I', capture=True).split()
    print('\nINSTALLATION COMPLETE\n')
    for ip in ips:
        if ':' not in ip:
            print(f'Open on your local network: http://{ip}:8787')
    if setup_key:
        print('\nFirst-run setup key (keep private): '+setup_key)
        print('Open the page and create your superuser. Recover this key with: sudo cat /etc/zedair/setup-key')
    if not setup_key:
        print('If setup is not finished, retrieve the key: sudo cat /etc/zedair/setup-key')
    print('\nNo firewall rules were changed. If the page cannot connect, see README.md for a LAN-only UFW rule.')
    print('If you close the laptop lid, use README.md to disable suspend-on-lid-close.')


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        raise SystemExit('Installation stopped: '+str(exc)+'\nNo formatting was performed. Read the error before rerunning.')
