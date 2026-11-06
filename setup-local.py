#!/usr/bin/env python3
"""Advertise zed.local using Avahi and forward HTTP port 80 to the existing Zedair service."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess

MARKER='# Managed by Zedair Download Desk\n'
AVAHI=Path('/etc/avahi/avahi-daemon.conf')
SYSTEMD=Path('/etc/systemd/system')


def avahi_config(text):
    lines=text.splitlines();out=[];inside=False;found=False
    for line in lines:
        if re.match(r'\s*\[',line):
            inside=line.strip()=='[server]'
            out.append(line)
            if inside:
                found=True;out.extend(['host-name=zed','host-name-from-machine-id=no','domain-name=local'])
        elif inside and re.match(r'\s*#?\s*(host-name|host-name-from-machine-id|domain-name)\s*=',line):
            continue
        else:out.append(line)
    if not found:out.extend(['[server]','host-name=zed','host-name-from-machine-id=no','domain-name=local'])
    return '\n'.join(out)+'\n'


def units(binary, host, port):
    address=f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
    return {
        'zedair-local.socket':MARKER+'''[Unit]
Description=Zedair local HTTP port
[Socket]
ListenStream=80
BindIPv6Only=both
NoDelay=true
[Install]
WantedBy=sockets.target
''',
        'zedair-local.service':MARKER+f'''[Unit]
Description=Zedair local HTTP forwarder
Requires=zedair.service
After=zedair.service
[Service]
ExecStart={binary} {address}
DynamicUser=true
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
MemoryMax=32M
'''}


def run(*args):return subprocess.run(args,check=True)


def main():
    if os.geteuid()!=0:raise SystemExit('Run: sudo bash /opt/zedair/setup-local.sh')
    config=json.loads(Path('/etc/zedair/config.json').read_text())
    if config.get('tls_cert'):raise SystemExit('This helper is for HTTP installations. Keep your configured HTTPS endpoint and use the README manual DNS guidance.')
    host=config.get('host','0.0.0.0');port=config.get('port',8787)
    if host not in ('0.0.0.0','127.0.0.1','::','::1') or type(port) is not int or not 1024<=port<=65535:
        raise SystemExit('This helper expects the default local Zedair listener on an unprivileged port. See README.')
    host={'0.0.0.0':'127.0.0.1','::':'::1'}.get(host,host)
    binary=next((p for p in ('/usr/lib/systemd/systemd-socket-proxyd','/lib/systemd/systemd-socket-proxyd') if Path(p).exists()),None)
    if not binary:raise SystemExit('systemd-socket-proxyd is missing from this Ubuntu installation.')
    for name in units(binary,host,port):
        path=SYSTEMD/name
        if path.exists() and not path.read_text().startswith(MARKER):raise SystemExit('Refusing to replace an unmanaged service: '+str(path))
    if (SYSTEMD/'zedair-local.socket').exists():
        subprocess.run(['systemctl','stop','zedair-local.socket','zedair-local.service'],check=False)
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
        try:probe.bind(('0.0.0.0',80))
        except OSError:raise SystemExit('Port 80 is already used by another service. Nothing was replaced; resolve the conflict first.')
    run('apt-get','update');run('apt-get','install','-y','avahi-daemon','avahi-utils','libnss-mdns')
    local={a['local'] for interface in json.loads(subprocess.check_output(['ip','-j','address'])) for a in interface.get('addr_info',[])}
    try:
        resolution=subprocess.run(['avahi-resolve-host-name','-4','zed.local'],capture_output=True,text=True,timeout=6)
        if resolution.returncode==0 and resolution.stdout.split()[-1] not in local:
            raise SystemExit('Another device already advertises zed.local. Resolve that name conflict first.')
    except subprocess.TimeoutExpired:pass
    original=AVAHI.read_text();backup=AVAHI.with_name('avahi-daemon.conf.zedair-before')
    if not backup.exists():shutil.copy2(AVAHI,backup)
    try:
        AVAHI.write_text(avahi_config(original))
        for name,content in units(binary,host,port).items():(SYSTEMD/name).write_text(content)
        run('systemctl','daemon-reload')
        run('systemctl','enable','--now','avahi-daemon','zedair-local.socket')
        run('systemctl','restart','avahi-daemon')
        run('systemctl','is-active','--quiet','zedair-local.socket','avahi-daemon')
    except Exception:
        AVAHI.write_text(original)
        subprocess.run(['systemctl','restart','avahi-daemon'],check=False)
        raise
    Path('/etc/zedair/local-address-enabled').write_text('http://zed.local\n')
    print('Open http://zed.local on your LAN. The old IP:port address still works.')
    print('No firewall or router rules were changed. See README if another device cannot resolve the name.')


if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('Local-address setup stopped: '+str(exc))
