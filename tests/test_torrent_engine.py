"""qBittorrent adapter, install helpers and Transmission import, over real HTTP to a fake WebUI."""
import base64
import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import torrent_migrate
import torrent_service
from server import APIError
from torrent_engine import QBittorrent, bdecode, magnet_hash, preferences, torrent_hash, STATUS
from torrent_fixtures import FakeQBittorrent, benc, make_torrent

H1 = 'a' * 40
H2 = 'b' * 40
SETTINGS = {'simultaneous_downloads': 1, 'torrent_down_kib': 0, 'torrent_up_kib': 256, 'seed_ratio': 0.0}


class ClientCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeQBittorrent()
        self.addCleanup(self.fake.close)
        self.bt = QBittorrent(self.fake.config(), APIError)


class SessionTests(ClientCase):
    def test_one_login_is_reused_and_requests_carry_matching_origin(self):
        self.bt.list();self.bt.list()
        self.assertEqual(self.fake.logins, 1)
        _, _, referer, origin, _ = self.fake.calls[0]
        self.assertEqual(origin, self.fake.url)
        self.assertTrue(referer.startswith(self.fake.url))

    def test_wrong_password_is_reported_even_though_qbittorrent_answers_200(self):
        bad = QBittorrent(self.fake.config(rpc_password='wrong'), APIError)
        with self.assertRaisesRegex(APIError, 'credentials') as ctx:
            bad.list()
        self.assertEqual(ctx.exception.status, 503)

    def test_expired_session_logs_in_again_transparently(self):
        self.fake.add_torrent(H1)
        self.assertEqual(len(self.bt.list()), 1)
        self.fake.session = None  # engine restarted: every cookie is now invalid
        self.assertEqual(len(self.bt.list()), 1)
        self.assertEqual(self.fake.logins, 2)

    def test_engine_offline_gives_a_clear_service_hint(self):
        offline = QBittorrent({'rpc_url': 'http://127.0.0.1:1', 'rpc_password': 'x'}, APIError)
        with self.assertRaisesRegex(APIError, 'zedair-qbittorrent') as ctx:
            offline.list()
        self.assertEqual(ctx.exception.status, 503)

    def test_legacy_transmission_rpc_url_in_existing_config_still_works(self):
        legacy = QBittorrent(self.fake.config(rpc_url=self.fake.url + '/transmission/rpc'), APIError)
        self.assertEqual(legacy.list(), [])


class MappingTests(ClientCase):
    def test_every_qbittorrent_state_maps_to_the_ui_status_codes(self):
        expected = {'downloading': 4, 'metaDL': 4, 'stalledDL': 4, 'forcedDL': 4, 'queuedDL': 3,
                    'uploading': 6, 'stalledUP': 6, 'forcedUP': 6, 'queuedUP': 5,
                    'pausedDL': 0, 'pausedUP': 0, 'stoppedDL': 0, 'stoppedUP': 0, 'error': 0, 'missingFiles': 0,
                    'checkingDL': 2, 'checkingUP': 2, 'checkingResumeData': 2, 'moving': 2, 'allocating': 2}
        for state, code in expected.items():
            self.assertEqual(STATUS[state], code, state)
        self.assertEqual(QBittorrent.normal({'hash': H1, 'state': 'some-future-state'})['status'], 0)

    def test_fields_match_what_the_browser_and_safety_checks_read(self):
        self.fake.add_torrent(H1, name='Movie', save_path='/srv/Zedair/Torrents', state='downloading', progress=0.25,
                              dlspeed=2048, upspeed=10, eta=90, ratio=1.5, total_size=900, size=1000, priority=2)
        t = self.bt.list()[0]
        self.assertEqual((t['hashString'], t['name'], t['status'], t['percentDone']), (H1, 'Movie', 4, 0.25))
        self.assertEqual((t['rateDownload'], t['rateUpload'], t['eta']), (2048, 10, 90))
        self.assertEqual((t['totalSize'], t['uploadRatio'], t['downloadDir'], t['queuePosition']), (900, 1.5, '/srv/Zedair/Torrents', 2))

    def test_infinite_eta_and_missing_metadata(self):
        self.fake.add_torrent(H1, state='metaDL', eta=8640000)
        t = self.bt.list()[0]
        self.assertEqual(t['eta'], -1)
        self.assertEqual(t['metadataPercentComplete'], 0)

    def test_error_states_carry_a_readable_message(self):
        self.fake.add_torrent(H1, state='missingFiles')
        self.fake.add_torrent(H2, state='error')
        problems = {t['hashString']: t['errorString'] for t in self.bt.list()}
        self.assertIn('missing', problems[H1])
        self.assertIn('error', problems[H2])

    def test_detail_reports_files_and_which_are_wanted(self):
        self.fake.add_torrent(H1, files=3, wanted=[0, 2], progress=0.5)
        d = self.bt.detail(H1)
        self.assertEqual([f['name'] for f in d['files']], ['Sample/f0.bin', 'Sample/f1.bin', 'Sample/f2.bin'])
        self.assertEqual([s['wanted'] for s in d['fileStats']], [True, False, True])
        self.assertEqual(d['files'][0]['bytesCompleted'], 25)

    def test_unknown_torrent_is_404(self):
        with self.assertRaises(APIError) as ctx:
            self.bt.detail(H1)
        self.assertEqual(ctx.exception.status, 404)

    def test_managed_lists_files_for_every_torrent_and_tolerates_a_vanished_one(self):
        self.fake.add_torrent(H1, save_path='/r', files=2)
        self.fake.add_torrent(H2, save_path='/r', files=1)
        del self.fake.files[H2]  # removed between the two calls -> 404 on /files
        managed = self.bt.managed()
        self.assertEqual(managed, [{'downloadDir': '/r', 'files': [{'name': 'Sample/f0.bin'}, {'name': 'Sample/f1.bin'}]}])


class IdentifierSafetyTests(ClientCase):
    def test_only_exact_forty_hex_hashes_reach_qbittorrent(self):
        # qBittorrent treats hashes=all as "every torrent" and '|' as a list.
        for bad in ('all', '', 'ALL', H1 + '|' + H2, H1[:-1], H1 + 'a', 'g' * 40, None, 7, ['a'*40], H1 + '\n'):
            with self.subTest(bad=bad), self.assertRaises(APIError) as ctx:
                self.bt.remove(bad)
            self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.fake.calls, [])

    def test_upper_case_hash_is_normalised(self):
        self.fake.add_torrent(H1)
        self.assertEqual(self.bt.get(H1.upper())['hashString'], H1)


class ActionTests(ClientCase):
    def test_pause_and_resume_on_qbittorrent_4(self):
        self.bt.stop([H1]);self.bt.start([H1, H2])
        self.assertEqual([c[0] for c in self.fake.calls if c[0].startswith('torrents/')], ['torrents/pause', 'torrents/resume'])
        self.assertEqual(self.fake.calls[-1][1]['hashes'], H1 + '|' + H2)

    def test_stop_and_start_on_qbittorrent_5(self):
        self.fake.api = '2.11.2'
        self.bt.stop([H1]);self.bt.start([H1])
        self.assertEqual([c[0] for c in self.fake.calls if c[0].startswith('torrents/')], ['torrents/stop', 'torrents/start'])

    def test_wrong_version_guess_falls_back_to_the_other_spelling(self):
        self.bt.api = (2, 9)
        self.fake.api = '2.11.0'
        self.bt.stop([H1])
        self.assertEqual(self.bt.api, (2, 11))
        self.assertIn('torrents/stop', self.fake.paths())

    def test_empty_id_list_sends_nothing(self):
        self.bt.stop([])
        self.assertEqual(self.fake.calls, [])

    def test_remove_never_deletes_downloaded_files(self):
        self.bt.remove(H1)
        call = [c for c in self.fake.calls if c[0] == 'torrents/delete'][0]
        self.assertEqual(call[1]['deleteFiles'], 'false')
        self.assertEqual(call[4], 'POST')

    def test_recheck(self):
        self.bt.recheck(H1)
        self.assertIn('torrents/recheck', self.fake.paths())

    def test_file_selection_raises_wanted_first_then_skips_the_rest(self):
        self.bt.set_files(H1, [1], 3)
        calls = [(c[1]['id'], c[1]['priority']) for c in self.fake.calls if c[0] == 'torrents/filePrio']
        self.assertEqual(calls, [('1', '1'), ('0|2', '0')])

    def test_empty_selection_skips_everything_and_never_sends_an_empty_id(self):
        self.bt.set_files(H1, [], 2)
        calls = [(c[1]['id'], c[1]['priority']) for c in self.fake.calls if c[0] == 'torrents/filePrio']
        self.assertEqual(calls, [('0|1', '0')])

    def test_selecting_everything_only_sets_wanted(self):
        self.bt.set_files(H1, [0, 1], 2)
        calls = [(c[1]['id'], c[1]['priority']) for c in self.fake.calls if c[0] == 'torrents/filePrio']
        self.assertEqual(calls, [('0|1', '1')])


class AddTests(ClientCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data, self.ident = make_torrent(Path(self.tmp.name), 'Pack', [('a.bin', b'x'*40000), ('b.bin', b'y'*100)])

    def test_torrent_file_is_uploaded_as_multipart_with_save_path_and_paused_flags(self):
        self.assertEqual(self.bt.add('/srv/Zedair/Torrents', metainfo=self.data, paused=True), self.ident)
        call = [c for c in self.fake.calls if c[0] == 'torrents/add'][0]
        body = call[1]['__multipart__']
        self.assertIn(b'filename="upload.torrent"', body)
        self.assertIn(self.data, body)
        self.assertIn(b'/srv/Zedair/Torrents', body)
        self.assertIn(b'name="paused"\r\n\r\ntrue', body)
        self.assertIn(b'name="stopped"\r\n\r\ntrue', body)  # qBittorrent 5 spelling
        self.assertIn(b'name="autoTMM"\r\n\r\nfalse', body)

    def test_magnet_is_sent_as_url_and_returns_its_hash(self):
        magnet = f'magnet:?xt=urn:btih:{H1.upper()}&dn=x'
        self.assertEqual(self.bt.add('/r', magnet=magnet), H1)
        body = [c for c in self.fake.calls if c[0] == 'torrents/add'][0][1]['__multipart__']
        self.assertIn(magnet.encode(), body)

    def test_duplicate_is_refused_before_anything_is_uploaded(self):
        self.fake.add_torrent(self.ident)
        with self.assertRaisesRegex(APIError, 'already') as ctx:
            self.bt.add('/r', metainfo=self.data)
        self.assertEqual(ctx.exception.status, 409)
        self.assertNotIn('torrents/add', self.fake.paths())

    def test_engine_refusal_is_reported(self):
        self.fake.add_result = 'Fails.'
        with self.assertRaises(APIError) as ctx:
            self.bt.add('/r', metainfo=self.data)
        self.assertEqual(ctx.exception.status, 409)

    def test_garbage_torrent_is_rejected_locally(self):
        for junk in (b'', b'd', b'not a torrent', b'de', b'd4:infoi1ee', b'd4:info' + b'd' * 100 + b'e'):
            with self.subTest(junk=junk[:12]), self.assertRaises(ValueError):
                self.bt.add('/r', metainfo=junk)
        self.assertNotIn('torrents/add', self.fake.paths())


class BencodeTests(unittest.TestCase):
    def test_info_hash_matches_sha1_of_the_info_dictionary(self):
        info = {'name': 'x', 'piece length': 16384, 'pieces': b'\x00' * 20, 'length': 5}
        self.assertEqual(torrent_hash(benc({'announce': 'http://t/a', 'info': info})), hashlib.sha1(benc(info)).hexdigest())

    def test_hash_is_independent_of_other_top_level_keys(self):
        info = {'name': 'x', 'piece length': 16384, 'pieces': b'\x00' * 20, 'length': 5}
        a = torrent_hash(benc({'info': info}))
        b = torrent_hash(benc({'announce': 'http://t', 'comment': 'zz', 'info': info}))
        self.assertEqual(a, b)

    def test_hostile_inputs_fail_cleanly(self):
        deep = b'l' * 5000 + b'e' * 5000
        for data in (deep, b'i9' * 10, b'5:ab', b'x', b'd1:a', b'l', b'999999999999:a', b'di1e1:ae'):
            with self.subTest(data=data[:12]), self.assertRaises(ValueError):
                bdecode(data)
        with self.assertRaises(ValueError):
            bdecode(benc({'a': 1}) + b'trailing')

    def test_v2_only_torrent_uses_truncated_sha256(self):
        info = {'name': 'x', 'piece length': 16384, 'file tree': {'x': {'': {'length': 1}}}, 'meta version': 2}
        self.assertEqual(torrent_hash(benc({'info': info})), hashlib.sha256(benc(info)).hexdigest()[:40])

    def test_magnet_hash_hex_and_base32(self):
        self.assertEqual(magnet_hash(f'magnet:?xt=urn:btih:{H1.upper()}'), H1)
        raw = bytes(range(20))
        b32 = base64.b32encode(raw).decode()
        self.assertEqual(magnet_hash(f'magnet:?dn=x&xt=urn:btih:{b32}'), raw.hex())
        for junk in ('magnet:?dn=x', 'magnet:?xt=urn:btih:zzz', 'magnet:?xt=urn:sha1:' + H1):
            self.assertEqual(magnet_hash(junk), '')


class PreferenceTests(unittest.TestCase):
    def test_limits_are_converted_to_bytes_and_upload_is_never_zero(self):
        p = preferences({**SETTINGS, 'torrent_down_kib': 500, 'torrent_up_kib': 1}, '/srv/Zedair/Torrents')
        self.assertEqual((p['dl_limit'], p['up_limit']), (500*1024, 1024))
        self.assertEqual(preferences(SETTINGS, '/x')['dl_limit'], 0)  # 0 = unlimited

    def test_queue_and_seeding_policy_matches_the_desk_settings(self):
        p = preferences({**SETTINGS, 'simultaneous_downloads': 20, 'seed_ratio': 1.5}, '/x')
        self.assertEqual((p['max_active_downloads'], p['max_active_uploads'], p['max_active_torrents']), (4, 1, 5))
        self.assertEqual((p['max_ratio_enabled'], p['max_ratio'], p['max_ratio_act']), (True, 1.5, 0))
        self.assertEqual(preferences(SETTINGS, '/x')['max_ratio'], 0.0)

    def test_low_memory_profile_for_a_2_gb_host(self):
        p = preferences(SETTINGS, '/srv/Zedair/Torrents')
        self.assertEqual(p['disk_io_type'], 2)          # POSIX I/O, not memory-mapped files
        self.assertLessEqual(p['async_io_threads'], 2)
        self.assertLessEqual(p['file_pool_size'], 40)
        self.assertLessEqual(p['checking_memory_use'], 16)
        self.assertLessEqual(p['max_connec'], 100)
        self.assertFalse(p['upnp']);self.assertFalse(p['lsd']);self.assertFalse(p['random_port'])
        self.assertTrue(p['dht'])  # magnets need it
        self.assertEqual(p['save_path'], '/srv/Zedair/Torrents')
        for key in ('temp_path_enabled', 'auto_tmm_enabled', 'incomplete_files_ext', 'preallocate_all'):
            self.assertIs(p[key], False)

    def test_preferences_are_posted_as_one_json_form_field(self):
        QBittorrent(self.fake_config(), APIError).apply_settings(SETTINGS, '/x')
        call = [c for c in self.fake.calls if c[0] == 'app/setPreferences'][0]
        self.assertEqual(call[4], 'POST')
        self.assertEqual(json.loads(call[1]['json'])['disk_io_type'], 2)

    def fake_config(self):
        self.fake = FakeQBittorrent()
        self.addCleanup(self.fake.close)
        return self.fake.config()


class ServiceFileTests(unittest.TestCase):
    def test_password_hash_uses_qbittorrents_pbkdf2_format(self):
        value = torrent_service.password_hash('correct horse', salt=b'0123456789abcdef')
        self.assertTrue(value.startswith('@ByteArray(') and value.endswith(')'))
        salt, digest = value[11:-1].split(':')
        self.assertEqual(base64.b64decode(salt), b'0123456789abcdef')
        self.assertEqual(base64.b64decode(digest), hashlib.pbkdf2_hmac('sha512', b'correct horse', b'0123456789abcdef', 100000, 64))

    def test_profile_is_loopback_only_authenticated_and_legal_notice_accepted(self):
        text = torrent_service.profile_config({'rpc_url': 'http://127.0.0.1:9092', 'rpc_user': 'zedair', 'rpc_password': 'pw'})
        for line in ('[LegalNotice]', 'Accepted=true', 'WebUI\\Address=127.0.0.1', 'WebUI\\Port=9092', 'WebUI\\Username=zedair',
                     'WebUI\\LocalHostAuth=true', 'WebUI\\CSRFProtection=true', 'WebUI\\HostHeaderValidation=true'):
            self.assertIn(line, text)
        self.assertNotIn('pw"', text)  # only the hash is stored
        self.assertNotIn('Address=0.0.0.0', text)

    def test_legacy_rpc_url_with_transmission_path_still_yields_the_port(self):
        text = torrent_service.profile_config({'rpc_url': 'http://127.0.0.1:9092/transmission/rpc', 'rpc_user': 'u', 'rpc_password': 'p'})
        self.assertIn('WebUI\\Port=9092\n', text)

    def test_existing_profile_is_never_overwritten(self):
        owner = types.SimpleNamespace(pw_uid=__import__('os').getuid(), pw_gid=__import__('os').getgid())
        with tempfile.TemporaryDirectory() as tmp:
            config = {'rpc_url': 'http://127.0.0.1:9092', 'rpc_user': 'u', 'rpc_password': 'p'}
            torrent_service.write_profile(tmp, config, owner)
            conf = Path(tmp)/'qbittorrent/qBittorrent/config/qBittorrent.conf'
            self.assertEqual(oct(conf.stat().st_mode & 0o777), '0o600')
            conf.write_text('[Preferences]\nUser=tuned\n')
            torrent_service.write_profile(tmp, config, owner)
            self.assertEqual(conf.read_text(), '[Preferences]\nUser=tuned\n')

    def test_engine_unit_limits_memory_and_stops_with_the_ssd(self):
        unit = torrent_service.qbittorrent_unit('/var/lib/zedair', '/srv/zedair-ssd', 'srv-zedair\\x2dssd.mount')
        for line in ('MemoryHigh=300M', 'MemoryMax=450M', 'MALLOC_ARENA_MAX=2', 'BindsTo=srv-zedair\\x2dssd.mount',
                     'RequiresMountsFor=/srv/zedair-ssd', 'ExecStart=/usr/bin/qbittorrent-nox --profile=/var/lib/zedair/qbittorrent',
                     'ProtectSystem=strict', 'NoNewPrivileges=true', 'ReadWritePaths=/var/lib/zedair /srv/zedair-ssd',
                     'LimitNOFILE=', 'OOMScoreAdjust=', 'Nice=10'):
            self.assertIn(line, unit)
        self.assertNotIn('transmission', unit.lower())

    def test_desk_unit_depends_on_the_new_engine_only(self):
        unit = torrent_service.desk_unit('/opt/zedair', '/etc/zedair/config.json', '/var/lib/zedair', '/srv/zedair-ssd')
        self.assertIn('Wants=network-online.target zedair-qbittorrent.service', unit)
        self.assertNotIn('transmission', unit)
        self.assertIn('MemoryMax=1000M', unit)

    def test_select_engine_flips_the_desk_unit_for_rollback(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(torrent_service, 'UNITS', Path(tmp)), patch.object(torrent_service, 'systemctl') as ctl:
            (Path(tmp)/'zedair.service').write_text('After=x zedair-qbittorrent.service\nWants=zedair-qbittorrent.service\n')
            (Path(tmp)/'zedair-transmission.service').write_text('[Unit]\n')
            torrent_service.select_engine('transmission')
            self.assertEqual((Path(tmp)/'zedair.service').read_text(), 'After=x zedair-transmission.service\nWants=zedair-transmission.service\n')
            self.assertIn(unittest.mock.call('disable', '--now', 'zedair-qbittorrent.service', check=False), ctl.mock_calls)
            self.assertIn(unittest.mock.call('enable', 'zedair-transmission.service', check=False), ctl.mock_calls)


class ImportTests(ClientCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)/'state'
        self.root = Path(self.tmp.name)/'ssd/Zedair'
        (self.root/'Torrents').mkdir(parents=True)
        (self.state/'transmission/torrents').mkdir(parents=True)
        (self.state/'transmission/resume').mkdir()
        (self.state/'qbittorrent').mkdir()
        self.data, self.ident = make_torrent(self.root/'Torrents', 'Pack', [('a.bin', b'a'*30000), ('b.bin', b'b'*30000), ('c.bin', b'c'*10)])

    def old(self, stem, destination, paused=0, dnd=None, torrent=None, suffix='.torrent'):
        (self.state/'transmission/torrents'/(stem+suffix)).write_bytes(torrent if torrent is not None else self.data)
        resume = {'destination': str(destination), 'paused': paused}
        if dnd is not None:
            resume['dnd'] = dnd
        (self.state/'transmission/resume'/(stem+'.resume')).write_bytes(benc(resume))

    def run_import(self):
        return torrent_migrate.import_transmission(self.bt, self.state, self.root, log=lambda *a: None)

    def prio(self):
        return [(c[1]['id'], c[1]['priority']) for c in self.fake.calls if c[0] == 'torrents/filePrio']

    def test_active_torrent_keeps_folder_and_is_verified_then_started(self):
        self.old('Pack.1234', self.root/'Torrents')
        summary = self.run_import()
        self.assertEqual((summary['added'], summary['skipped']), (1, []))
        paths = self.fake.paths()
        self.assertLess(paths.index('torrents/add'), paths.index('torrents/recheck'))
        self.assertLess(paths.index('torrents/recheck'), paths.index('torrents/resume'))
        body = [c for c in self.fake.calls if c[0] == 'torrents/add'][0][1]['__multipart__']
        self.assertIn(str(self.root/'Torrents').encode(), body)
        self.assertIn(b'name="paused"\r\n\r\ntrue', body)  # added paused so file selection applies before any download

    def test_paused_torrent_stays_paused_and_unwanted_files_are_skipped_before_start(self):
        self.old('Pack.1234', self.root/'Torrents', paused=1, dnd=[0, 1, 1])
        self.run_import()
        self.assertEqual(self.prio(), [('0', '1'), ('1|2', '0')])
        self.assertNotIn('torrents/resume', self.fake.paths())

    def test_runs_only_once_and_never_touches_the_old_data(self):
        self.old('Pack.1234', self.root/'Torrents')
        before = sorted(p.name for p in (self.state/'transmission').rglob('*'))
        self.assertIsNotNone(self.run_import())
        self.assertIsNone(self.run_import())
        self.assertEqual(self.fake.paths().count('torrents/add'), 1)
        self.assertEqual(sorted(p.name for p in (self.state/'transmission').rglob('*')), before)
        self.assertTrue((self.state/'qbittorrent/transmission-import.json').is_file())

    def test_nothing_to_import_does_nothing(self):
        import shutil
        shutil.rmtree(self.state/'transmission')
        self.assertIsNone(self.run_import())
        self.assertEqual(self.fake.calls, [])

    def test_folders_outside_the_zedair_root_are_skipped_with_a_reason(self):
        self.old('Pack.1234', '/home/other')
        summary = self.run_import()
        self.assertEqual(summary['added'], 0)
        self.assertEqual(summary['skipped'][0][0], 'Pack.1234.torrent')
        self.assertNotIn('torrents/add', self.fake.paths())

    def test_already_present_torrents_are_counted_not_duplicated(self):
        self.fake.add_torrent(self.ident)
        self.old('Pack.1234', self.root/'Torrents')
        summary = self.run_import()
        self.assertEqual((summary['added'], summary['already_present']), (0, 1))

    def test_corrupt_files_do_not_block_the_others(self):
        self.old('Bad.0001', self.root/'Torrents', torrent=b'garbage')
        self.old('Pack.1234', self.root/'Torrents')
        summary = self.run_import()
        self.assertEqual(summary['added'], 1)
        self.assertEqual([name for name, _ in summary['skipped']], ['Bad.0001.torrent'])

    def test_magnet_without_metadata_is_imported(self):
        magnet = f'magnet:?xt=urn:btih:{H2}&dn=m'
        self.old('m.0001', self.root/'Torrents', torrent=magnet.encode(), suffix='.magnet')
        self.assertEqual(self.run_import()['added'], 1)
        self.assertIn(magnet.encode(), [c for c in self.fake.calls if c[0] == 'torrents/add'][0][1]['__multipart__'])

    def test_offline_engine_aborts_without_a_marker_so_it_retries_later(self):
        self.old('Pack.1234', self.root/'Torrents')
        offline = QBittorrent({'rpc_url': 'http://127.0.0.1:1'}, APIError)
        with self.assertRaises(APIError):
            torrent_migrate.import_transmission(offline, self.state, self.root, log=lambda *a: None)
        self.assertFalse((self.state/'qbittorrent/transmission-import.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
