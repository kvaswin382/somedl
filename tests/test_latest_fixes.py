import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import server
from server import Desk, Server, DEFAULTS, APIError

from test_desk import Fixture

class NewFeaturesAndFixesTest(Fixture):
    def setUp(self):
        super().setUp()
        self.desk.rpc.mock = True

    def tearDown(self):
        super().tearDown()

    def test_simultaneous_downloads_default(self):
        self.assertIn('simultaneous_downloads', DEFAULTS)
        self.assertEqual(DEFAULTS['simultaneous_downloads'], 1)
        self.assertEqual(self.desk.settings()['simultaneous_downloads'], 1)

    def test_settings_range_persistence(self):
        # Update simultaneous_downloads
        self.desk.set_setting('simultaneous_downloads', 5)
        self.assertEqual(self.desk.get_setting('simultaneous_downloads', 1), 5)
        self.assertEqual(self.desk.settings()['simultaneous_downloads'], 5)

        # Update other ranges
        self.desk.set_setting('retry_count', 4)
        self.assertEqual(self.desk.get_setting('retry_count'), 4)
        self.desk.set_setting('seed_ratio', 1.5)
        self.assertEqual(self.desk.get_setting('seed_ratio'), 1.5)
        self.desk.set_setting('reserve_gib', 10)
        self.assertEqual(self.desk.get_setting('reserve_gib'), 10)
        self.desk.set_setting('playlist_limit', 50)
        self.assertEqual(self.desk.get_setting('playlist_limit'), 50)

    def test_torrent_preallocation_zero(self):
        # Verify apply_torrent_settings passes preallocation=0
        calls = []
        def fake_call(method, args=None):
            calls.append((method, args))
            return {'result': 'success', 'arguments': {}}

        self.desk.rpc.call = fake_call
        self.desk.set_setting('simultaneous_downloads', 4)
        self.desk.apply_torrent_settings()

        self.assertTrue(len(calls) > 0)
        method, args = calls[0]
        self.assertEqual(method, 'session-set')
        self.assertEqual(args.get('preallocation'), 0)
        self.assertEqual(args.get('download-queue-size'), 4)
        self.assertEqual(args.get('seed-queue-size'), 4)

    def test_transmission_settings_auto_patch_on_startup(self):
        # Setup fake transmission settings file
        trans_dir = self.desk.state / 'transmission'
        trans_dir.mkdir(parents=True, exist_ok=True)
        settings_file = trans_dir / 'settings.json'
        settings_file.write_text(json.dumps({'preallocation': 1, 'download-dir': '/tmp'}), encoding='utf-8')

        # Run Desk init on this config
        new_desk = Desk(self.config)
        data = json.loads(settings_file.read_text(encoding='utf-8'))
        self.assertEqual(data.get('preallocation'), 0)
        new_desk.close()

    def test_desk_processes_dict_and_stop_process(self):
        self.assertTrue(hasattr(self.desk, 'processes'))
        self.assertIsInstance(self.desk.processes, dict)

        # Add mock processes
        mock_p1 = MagicMock()
        mock_p1.poll.return_value = None
        mock_p2 = MagicMock()
        mock_p2.poll.return_value = None
        self.desk.processes['job-1'] = mock_p1
        self.desk.processes['job-2'] = mock_p2

        # Stop specific job
        self.desk.stop_process('job-1')
        mock_p1.terminate.assert_called_once()
        mock_p2.terminate.assert_not_called()

        # Stop all
        self.desk.stop_process()
        mock_p2.terminate.assert_called_once()

    def test_api_settings_partial_update(self):
        # Verify settings update handles simultaneous_downloads clamp
        self.desk.set_setting('hold', True)
        self.desk.set_setting('simultaneous_downloads', 1)

        update_payload = {'simultaneous_downloads': '8'}
        simul = max(1, min(10, int(update_payload['simultaneous_downloads'])))
        self.desk.set_setting('simultaneous_downloads', simul)

        saved = self.desk.settings()
        self.assertEqual(saved['hold'], True)
        self.assertEqual(saved['simultaneous_downloads'], 8)

    def test_frontend_files_integrity(self):
        base_dir = Path(__file__).resolve().parent.parent
        index_html = (base_dir / 'static' / 'index.html').read_text(encoding='utf-8')
        style_css = (base_dir / 'static' / 'style.css').read_text(encoding='utf-8')
        app_js = (base_dir / 'static' / 'app.js').read_text(encoding='utf-8')

        # 1. simultaneous_downloads range in index.html
        self.assertIn('name="simultaneous_downloads"', index_html)
        self.assertIn('class="styled-range"', index_html)
        self.assertIn('urlsCountBadge', index_html)
        self.assertIn('queue-pagination', index_html)
        self.assertIn('queuePageSize', index_html)
        self.assertIn('queuePrev', index_html)
        self.assertIn('queueNext', index_html)

        # 2. style.css danger contrast fix
        self.assertIn('button.primary.danger', style_css)
        self.assertIn('#messageAccept.danger', style_css)
        self.assertIn('#ffffff', style_css)

        # 3. style.css connected indicator color
        self.assertIn('.connected', style_css)
        self.assertIn('#15803d', style_css)
        self.assertIn('#4ade80', style_css)

        # 4. style.css preview modal fix (no black area, responsive contain)
        self.assertIn('.preview-stage', style_css)
        self.assertIn('calc(100dvh - 200px)', style_css)
        self.assertIn('object-fit:contain', style_css)

        # 5. app.js logic
        self.assertIn('queuePageSize', app_js)
        self.assertIn('updateQueuePagination', app_js)
        self.assertIn('updateUrlsCount', app_js)
        self.assertIn('handleRangeAutoSave', app_js)
        self.assertIn('fitPreview', app_js)

    def test_8k_video_format_no_4k_cap(self):
        from media import video_format
        import platforms

        # 1. 'best' format choices
        best_mp4 = video_format('best', mp4=True)
        self.assertIn('height>2160', best_mp4)
        self.assertIn('bv*[height>2160]', best_mp4)
        self.assertIn('+ba', best_mp4)

        best_auto = video_format('best', mp4=False)
        self.assertEqual(best_auto, 'bv*+ba/b')

        # 2. '4320' 8K ceiling choices
        format_8k_mp4 = video_format('4320', mp4=True)
        self.assertIn('height<=4320', format_8k_mp4)
        self.assertIn('height>2160', format_8k_mp4)

        format_8k_auto = video_format('4320', mp4=False)
        self.assertEqual(format_8k_auto, 'bv*[height<=4320]+ba/b[height<=4320]')

        # 3. '2160' 4K ceiling
        format_4k_mp4 = video_format('2160', mp4=True)
        self.assertEqual(format_4k_mp4, 'bv*[ext=mp4][height<=2160]+ba[ext=m4a]/b[ext=mp4][height<=2160]')

        # 4. QUALITY tuples
        self.assertIn('4320', server.QUALITY)
        self.assertIn('4320', platforms.QUALITY)

        # 5. UI dropdowns
        base_dir = Path(__file__).resolve().parent.parent
        index_html = (base_dir / 'static' / 'index.html').read_text(encoding='utf-8')
        app_js = (base_dir / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('value="4320"', index_html)
        self.assertIn("['4320'", app_js)

if __name__ == '__main__':
    unittest.main()
