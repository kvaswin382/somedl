import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import urllib.error

import telegram_notifier
import browser_video
from server import DEFAULTS, Desk, APIError
from test_desk import Fixture

class TelegramNotifierTest(unittest.TestCase):
    def test_send_message_missing_token_or_recipients(self):
        res = telegram_notifier.send_message('', ['123'], 'hello')
        self.assertFalse(res['ok'])
        self.assertIn('token is required', res['error'])

        res = telegram_notifier.send_message('token123', [], 'hello')
        self.assertFalse(res['ok'])
        self.assertIn('log group ID', res['error'])

    @patch('urllib.request.urlopen')
    def test_send_message_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({'ok': True}).encode('utf-8')
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = telegram_notifier.send_message('bot123:token', ['-100123456', '987654'], 'Test message')
        self.assertTrue(res['ok'])
        self.assertEqual(res['successes'], ['-100123456', '987654'])
        self.assertEqual(mock_urlopen.call_count, 2)
        # Verify recipients isolation: only specified IDs were called
        req1 = mock_urlopen.call_args_list[0][0][0]
        self.assertIn(b'"chat_id": "-100123456"', req1.data)
        req2 = mock_urlopen.call_args_list[1][0][0]
        self.assertIn(b'"chat_id": "987654"', req2.data)

    @patch('urllib.request.urlopen')
    def test_send_snapshot_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        photo_data = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb'
        telegram_notifier.send_snapshot('bot123:token', ['-100123456'], photo_data, caption='Page snapshot')
        self.assertEqual(mock_urlopen.call_count, 1)
        req = mock_urlopen.call_args[0][0]
        self.assertIn(b'-100123456', req.data)
        self.assertIn(b'Page snapshot', req.data)
        self.assertIn(photo_data, req.data)


class TelegramSettingsTest(Fixture):
    def test_defaults(self):
        self.assertIn('telegram_bot_token', DEFAULTS)
        self.assertIn('telegram_chat_id', DEFAULTS)
        self.assertIn('telegram_admin_id', DEFAULTS)
        self.assertIn('telegram_snapshots', DEFAULTS)
        self.assertEqual(DEFAULTS['telegram_bot_token'], '')
        self.assertEqual(DEFAULTS['telegram_chat_id'], '')
        self.assertEqual(DEFAULTS['telegram_admin_id'], '')
        self.assertFalse(DEFAULTS['telegram_snapshots'])

    def test_save_and_retrieve_settings(self):
        self.desk.set_setting('telegram_bot_token', '123456:ABC-DEF')
        self.desk.set_setting('telegram_chat_id', '-100987654321')
        self.desk.set_setting('telegram_admin_id', '11223344')
        self.desk.set_setting('telegram_snapshots', True)

        s = self.desk.settings()
        self.assertEqual(s['telegram_bot_token'], '123456:ABC-DEF')
        self.assertEqual(s['telegram_chat_id'], '-100987654321')
        self.assertEqual(s['telegram_admin_id'], '11223344')
        self.assertTrue(s['telegram_snapshots'])


class BrowserEmbedTransferTest(unittest.TestCase):
    def test_pick_prioritizes_hlsplaylist(self):
        candidates = [
            {'url': 'https://v.redd.it/test1234/mp4_low.mp4', 'kind': 'video'},
            {'url': 'https://v.redd.it/test1234/HLSPlaylist.m3u8', 'kind': 'manifest'},
            {'url': 'https://v.redd.it/test1234/audio.m3u8', 'kind': 'manifest'},
        ]
        chosen = browser_video.pick(candidates, active=set(), quality='best', mp4=False)
        self.assertEqual(chosen['url'], 'https://v.redd.it/test1234/HLSPlaylist.m3u8')

    def test_pick_embed_priority(self):
        candidates = [
            {'url': 'https://www.redgifs.com/watch/cleantest123', 'kind': 'embed'},
            {'url': 'https://v.redd.it/test1234/mp4_low.mp4', 'kind': 'video'},
        ]
        chosen = browser_video.pick(candidates, active=set(), quality='best', mp4=False)
        self.assertEqual(chosen['kind'], 'embed')
        self.assertEqual(chosen['url'], 'https://www.redgifs.com/watch/cleantest123')

    @patch('browser_video.execute')
    def test_transfer_strips_foreign_referer_and_cookies_for_embed(self, mock_execute):
        mock_execute.return_value = (0, 'Success')
        with tempfile.TemporaryDirectory() as tmpdir:
            spec = {
                'url': 'https://www.reddit.com/r/community/comments/abc123/post_title/',
                'dest': tmpdir,
                'args': [
                    'yt_dlp',
                    '--user-agent', 'Mozilla/5.0...',
                    '--add-header', 'Referer:https://www.reddit.com/',
                    '--cookies', '/path/to/reddit_cookie.txt',
                    '-o', '%(title).120B [%(id)s].%(ext)s',
                    '--', 'https://www.reddit.com/r/community/comments/abc123/post_title/'
                ]
            }
            found = {
                'url': 'https://www.redgifs.com/watch/samplevideo',
                'kind': 'embed',
                'headers': {'referer': 'https://www.reddit.com/'}
            }
            import http.cookiejar
            jar = http.cookiejar.MozillaCookieJar()
            title = 'Clean Post Title'

            res = browser_video.transfer(spec, found, title, jar)
            self.assertEqual(res, 0)
            self.assertTrue(mock_execute.called)
            passed_args = mock_execute.call_args[0][0]

            # Verify no Reddit referer header was passed to the external embed
            for i, arg in enumerate(passed_args):
                if arg == '--add-header':
                    self.assertNotIn('referer:https://www.reddit.com/', passed_args[i+1].lower())
            # Verify Reddit cookies were NOT passed to the external embed
            self.assertNotIn('--cookies', passed_args)
            # Verify title was passed for replacement
            self.assertIn('--replace-in-metadata', passed_args)
            self.assertIn('title', passed_args)
            self.assertIn('Clean Post Title', passed_args)
            # Verify output filename template was updated to exact title
            o_idx = passed_args.index('-o')
            self.assertEqual(passed_args[o_idx + 1], '%(title).160B.%(ext)s')

if __name__ == '__main__':
    unittest.main()
