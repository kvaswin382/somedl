#!/usr/bin/env python3
"""Install a checksum-verified official Ookla binary locally, without running a test."""
import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

URL='https://packagecloud.io/ookla/speedtest-cli/packages/ubuntu/jammy/speedtest_1.2.0.84-1.ea6b6773cf_amd64.deb/download.deb?distro_version_id=237'
SHA256='35e084567a6388631fb10cf01e5e0d6b57a67d34ede2b72ba111b3d9164c8b94'

def main():
    if os.geteuid()!=0:raise SystemExit('Run: sudo bash install-speedtest.sh')
    if platform.machine()!='x86_64':raise SystemExit('This installer targets your x86_64 Zedair.')
    if not Path('/opt/zedair').is_dir():raise SystemExit('Install Zedair first.')
    if not shutil.which('dpkg-deb'):raise SystemExit('dpkg-deb is required (included with Ubuntu).')
    with tempfile.TemporaryDirectory(prefix='zedair-speedtest-') as temp:
        base=Path(temp);package=base/'speedtest.deb'
        print('Downloading official Ookla CLI 1.2.0 for x86_64…',flush=True)
        with urllib.request.urlopen(URL,timeout=60) as response,package.open('wb') as out:
            size=0
            while chunk:=response.read(65536):
                size+=len(chunk)
                if size>20*1024*1024:raise ValueError('Unexpected package size')
                out.write(chunk)
        if hashlib.sha256(package.read_bytes()).hexdigest()!=SHA256:
            raise ValueError('Package checksum mismatch; installation cancelled')
        subprocess.run(['dpkg-deb','--extract',str(package),str(base/'unpacked')],check=True)
        binary=base/'unpacked/usr/bin/speedtest'
        target=Path('/opt/zedair/bin/speedtest');target.parent.mkdir(exist_ok=True)
        pending=target.with_suffix('.new');shutil.copyfile(binary,pending);pending.chmod(0o755);os.replace(pending,target)
        docs=Path('/opt/zedair/speedtest-docs');docs.mkdir(exist_ok=True)
        shutil.copyfile(base/'unpacked/usr/share/man/man5/speedtest.5',docs/'speedtest.5')
        subprocess.run([str(target),'--version'],check=True)
    print('Installed. No test was run and no agreements were accepted.')
    print('Restart: sudo systemctl restart zedair')
    print('Then open Speed test and review the first-use agreement in the webpage.')

if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('Installation stopped: '+str(exc))
