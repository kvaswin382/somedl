"""Real qBittorrent-nox: bootstrap profile, tuned preferences, add/verify/select/remove, and the Desk loop."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import torrent_service
from server import APIError, Desk, digest
from torrent_engine import QBittorrent, preferences
import torrent_migrate
from torrent_fixtures import benc, make_torrent

BINARY = shutil.which('qbittorrent-nox')
if not BINARY:
    if os.environ.get('ZEDAIR_REQUIRE_QBT') == '1':
        raise SystemExit('qbittorrent-nox is required but not installed')
    print('SKIP: qbittorrent-nox not installed');raise SystemExit(0)


def wait(check, what, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError('timed out waiting for ' + what)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def main():
    tmp = Path(tempfile.mkdtemp(prefix='zedair-qbt-'))
    port = free_port()
    config = {'rpc_url': f'http://127.0.0.1:{port}', 'rpc_user': 'zedair', 'rpc_password': 'integration-secret-1'}
    owner = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
    process = None
    try:
        state, root = tmp/'state', tmp/'ssd/Zedair'
        (root/'Torrents').mkdir(parents=True)
        profile = torrent_service.write_profile(state, config, owner)
        process = subprocess.Popen([BINARY, f'--profile={profile}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        bt = QBittorrent(config, APIError)
        wait(lambda: not _fails(bt), 'WebUI to answer with the installer-generated PBKDF2 credentials')
        # wrong credentials are rejected
        try:
            QBittorrent({**config, 'rpc_password': 'nope'}, APIError).list();raise AssertionError('bad password accepted')
        except APIError as exc:
            assert exc.status == 503, exc

        settings = {'simultaneous_downloads': 2, 'torrent_down_kib': 400, 'torrent_up_kib': 64, 'seed_ratio': 0.0}
        bt.apply_settings(settings, root/'Torrents')
        _, body = bt.request('app/preferences')
        applied = json.loads(body)
        for key, value in preferences(settings, root/'Torrents').items():
            assert applied[key] == value, (key, applied[key], value)
        print('preferences applied and read back:', len(preferences(settings, root/'Torrents')))

        data, ident = make_torrent(root/'Torrents', 'Pack', [('a.bin', os.urandom(60000)), ('sub/b.bin', os.urandom(40000)), ('c.bin', os.urandom(1000))])
        assert bt.add(root/'Torrents', metainfo=data, paused=True) == ident
        bt.recheck(ident)  # a torrent added paused is not verified until asked (the importer does the same)
        wait(lambda: bt.get(ident)['percentDone'] == 1.0 or None, 'existing data to be recognised')
        t = bt.get(ident)
        assert t['status'] == 0 and t['downloadDir'] == str(root/'Torrents'), t   # paused as requested
        try:
            bt.add(root/'Torrents', metainfo=data);raise AssertionError('duplicate accepted')
        except APIError as exc:
            assert exc.status == 409

        d = bt.detail(ident)
        assert [f['name'] for f in d['files']] == ['Pack/a.bin', 'Pack/c.bin', 'Pack/sub/b.bin'] or len(d['files']) == 3, d['files']
        bt.set_files(ident, [0], 3)
        assert [s['wanted'] for s in bt.detail(ident)['fileStats']].count(True) == 1
        bt.set_files(ident, [0, 1, 2], 3)
        assert all(s['wanted'] for s in bt.detail(ident)['fileStats'])

        # ratio 0: resuming a complete torrent seeds briefly then stops again
        bt.start([ident])
        wait(lambda: bt.get(ident)['status'] in (5, 6) or None, 'torrent to start')
        wait(lambda: bt.get(ident)['status'] == 0 or None, 'seeding to stop at ratio 0')
        bt.recheck(ident)
        wait(lambda: bt.get(ident)['percentDone'] == 1.0 or None, 'verification')

        magnet = 'magnet:?xt=urn:btih:' + '0123456789abcdef' * 2 + '01234567&dn=nothing'
        mh = bt.add(root/'Torrents', magnet=magnet)
        wait(lambda: bt.get(mh)['status'] == 4 or None, 'magnet to start looking for metadata')
        assert bt.get(mh)['metadataPercentComplete'] == 0

        managed = bt.managed()
        assert any(Path(m['downloadDir'], f['name']) == root/'Torrents/Pack/a.bin' for m in managed for f in m['files'])

        # the full desk: loop publishes torrents, hold stops them, files are protected, removal keeps data
        desk = Desk({'state': str(state), 'mount': str(tmp/'ssd'), 'require_mount': False, **config, 'setup_key_hash': digest('k')})
        loop = threading.Thread(target=desk.torrent_loop, daemon=True);loop.start()
        try:
            wait(lambda: len(desk.torrents) == 2 or None, 'desk to list both torrents')
            wait(lambda: desk.torrent_error == '' or None, 'desk without engine error')
            desk.set_setting('hold', True);desk.torrent_wake.set()
            wait(lambda: all(t['status'] == 0 for t in desk.refresh_torrents()) or None, 'hold to stop the magnet')
            assert mh in desk.get_setting('blocked_torrents')
            desk.set_setting('hold', False);desk.torrent_wake.set()
            wait(lambda: any(t['hashString'] == mh and t['status'] != 0 for t in desk.refresh_torrents()) or None, 'release to restart the magnet')
            target = root/'Torrents/Pack/a.bin'
            st = target.stat()
            try:
                desk.delete_file({'confirm': True, 'path': 'Torrents/Pack/a.bin', 'stamp': f'{st.st_size}:{st.st_mtime_ns}'})
                raise AssertionError('torrent file was deletable')
            except APIError as exc:
                assert 'Remove this torrent' in str(exc)
            bt.remove(ident)
            wait(lambda: not any(t['hashString'] == ident for t in bt.list()) or None, 'removal')
            assert target.exists() and (root/'Torrents/Pack/sub/b.bin').exists(), 'removal deleted data'
            desk.delete_file({'confirm': True, 'path': 'Torrents/Pack/a.bin', 'stamp': f'{st.st_size}:{st.st_mtime_ns}'})
        finally:
            desk.stop.set();desk.torrent_wake.set();loop.join(10)
            desk.close()
        # one-time import of what the old Transmission engine left behind
        old = state/'transmission'
        (old/'torrents').mkdir(parents=True);(old/'resume').mkdir()
        data2, ident2 = make_torrent(root/'Torrents', 'Old', [('keep.bin', os.urandom(50000)), ('skip.bin', os.urandom(50000))])
        (old/'torrents/Old.abcd1234.torrent').write_bytes(data2)
        (old/'resume/Old.abcd1234.resume').write_bytes(benc({'destination': str(root/'Torrents'), 'paused': 1, 'dnd': [0, 1]}))
        summary = torrent_migrate.import_transmission(bt, state, root, log=lambda *a: None)
        assert summary['added'] == 1 and not summary['skipped'], summary
        wait(lambda: bt.get(ident2)['percentDone'] > 0 or None, 'imported torrent to be verified')
        imported = bt.detail(ident2)
        assert imported['status'] == 0, imported                      # was paused in Transmission
        assert [f['wanted'] for f in imported['fileStats']] == [True, False], imported['fileStats']
        assert torrent_migrate.import_transmission(bt, state, root, log=lambda *a: None) is None   # runs once
        assert (old/'torrents/Old.abcd1234.torrent').exists()         # never deletes the old state
        print('qBittorrent integration passed')
    finally:
        if process:
            process.terminate()
            try:process.wait(15)
            except subprocess.TimeoutExpired:process.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def _fails(bt):
    try:
        bt.list();return False
    except APIError:
        return True


if __name__ == '__main__':
    main()
