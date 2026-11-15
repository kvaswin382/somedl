#!/usr/bin/env python3
"""Install and upgrade support for the private qBittorrent-nox service (root only)."""
import base64
import hashlib
import os
import pwd
import subprocess
from pathlib import Path

UNITS = Path('/etc/systemd/system')
QBT_UNIT = 'zedair-qbittorrent.service'
OLD_UNIT = 'zedair-transmission.service'
DESK_UNIT = 'zedair.service'


def save(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.zedair-new')
    temp.write_text(content)
    temp.chmod(mode)
    os.replace(temp, path)


def password_hash(password, salt=None):
    """qBittorrent's WebUI password format: PBKDF2-HMAC-SHA512, 100000 rounds, 64 bytes."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha512', password.encode(), salt, 100000, 64)
    return '@ByteArray(%s:%s)' % (base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def profile_config(config):
    """Bootstrap only. The desk applies every runtime setting through the API on connect."""
    url = config.get('rpc_url', 'http://127.0.0.1:9092')
    port = url.rsplit(':', 1)[-1].split('/')[0]
    return (f'[LegalNotice]\nAccepted=true\n\n[Preferences]\n'
            f'WebUI\\Address=127.0.0.1\nWebUI\\Port={port}\n'
            f'WebUI\\Username={config["rpc_user"]}\n'
            f'WebUI\\Password_PBKDF2="{password_hash(config["rpc_password"])}"\n'
            'WebUI\\LocalHostAuth=true\nWebUI\\CSRFProtection=true\nWebUI\\HostHeaderValidation=true\n'
            'WebUI\\ClickjackingProtection=true\nWebUI\\UseUPnP=false\n'
            'WebUI\\SessionTimeout=3600\nWebUI\\MaxAuthenticationFailCount=5\n')


def write_profile(state, config, owner):
    """Create /var/lib/zedair/qbittorrent. An existing config is never overwritten."""
    profile = Path(state)/'qbittorrent'
    conf = profile/'qBittorrent'/'config'/'qBittorrent.conf'
    for folder in (profile, profile/'qBittorrent', conf.parent):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chown(folder, owner.pw_uid, owner.pw_gid)
    if not conf.exists():
        save(conf, profile_config(config), 0o600)
        os.chown(conf, owner.pw_uid, owner.pw_gid)
    return profile


def qbittorrent_unit(state, mount, mount_unit):
    return f'''[Unit]
Description=Zedair private torrent engine (qBittorrent-nox)
After=network-online.target {mount_unit}
Wants=network-online.target
RequiresMountsFor={mount}
BindsTo={mount_unit}

[Service]
Type=simple
User=zedair
Group=zedair
ExecStart=/usr/bin/qbittorrent-nox --profile={state}/qbittorrent
Restart=on-failure
RestartSec=10
TimeoutStopSec=60
UMask=0077
# Resource limits for a 2 GB machine shared with FFmpeg, yt-dlp and Chromium.
# Few arenas keeps glibc from multiplying RSS across libtorrent's threads.
Environment=MALLOC_ARENA_MAX=2
Nice=10
CPUWeight=50
IOSchedulingClass=best-effort
IOSchedulingPriority=6
MemoryHigh=300M
MemoryMax=450M
TasksMax=128
LimitNOFILE=8192
OOMScoreAdjust=300
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths={state} {mount}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
StandardOutput=journal
StandardError=journal
LogRateLimitIntervalSec=30s
LogRateLimitBurst=20

[Install]
WantedBy=multi-user.target
'''


def desk_unit(app, config_file, state, mount, engine_unit=QBT_UNIT):
    return f'''[Unit]
Description=Zedair Download Desk
After=network-online.target {engine_unit}
Wants=network-online.target {engine_unit}

[Service]
Type=simple
User=zedair
Group=zedair
WorkingDirectory={app}
Environment=PATH={app}/bin:{app}/venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={app}/venv/bin/python {app}/server.py --config {config_file}
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillMode=control-group
UMask=0077
Nice=10
CPUQuota=100%
MemoryHigh=750M
MemoryMax=1000M
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths={state} {mount}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
StandardOutput=journal
StandardError=journal
LogRateLimitIntervalSec=30s
LogRateLimitBurst=20

[Install]
WantedBy=multi-user.target
'''


def systemctl(*args, check=True):
    return subprocess.run(['systemctl', *args], check=check)


def mount_unit_for(mount):
    return subprocess.run(['systemd-escape', '--path', '--suffix=mount', str(mount)],
                          check=True, capture_output=True, text=True).stdout.strip()


def install_services(config, app, state, config_file):
    """Create or refresh the qBittorrent unit and the desk unit, retire Transmission."""
    owner = pwd.getpwnam('zedair')
    mount = Path(config['mount'])
    write_profile(state, config, owner)
    save(UNITS/QBT_UNIT, qbittorrent_unit(state, mount, mount_unit_for(mount)))
    save(UNITS/DESK_UNIT, desk_unit(app, config_file, state, mount))
    systemctl('daemon-reload')
    # Transmission would hold port 9092. Its unit file and data stay for rollback.
    systemctl('disable', '--now', OLD_UNIT, check=False)
    systemctl('enable', QBT_UNIT)


def select_engine(engine):
    """Point zedair.service at 'qbittorrent' or 'transmission' (used by rollback)."""
    source, target = (OLD_UNIT, QBT_UNIT) if engine == 'qbittorrent' else (QBT_UNIT, OLD_UNIT)
    unit = UNITS/DESK_UNIT
    if unit.exists():
        save(unit, unit.read_text().replace(source, target))
    systemctl('daemon-reload')
    systemctl('disable', '--now', source, check=False)
    if (UNITS/target).exists():
        systemctl('enable', target, check=False)
