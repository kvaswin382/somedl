"""Tests for Reddit downloading fixes: cookies, domains, age-gating, fallback, RedGIFs embeds, and image galleries."""
import http.cookiejar
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import browser_video
import media
import platforms
import server

class RedditSupportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_domains_and_platform_for(self):
        self.assertIn('reddit.com', platforms.DOMAINS['reddit'])
        self.assertIn('redd.it', platforms.DOMAINS['reddit'])
        self.assertEqual(platforms.platform_for('https://www.reddit.com/r/pics/comments/123/title/'), 'reddit')
        self.assertEqual(platforms.platform_for('https://old.reddit.com/r/community/comments/456/title/'), 'reddit')
        self.assertEqual(platforms.platform_for('https://redd.it/789'), 'reddit')
        self.assertEqual(platforms.platform_for('https://v.redd.it/abc123xyz'), 'reddit')

    def test_raw_fields_contains_reddit(self):
        self.assertIn('reddit', platforms.RAW_FIELDS)
        self.assertIn('reddit_session', platforms.RAW_FIELDS['reddit'])
        self.assertIn('token_v2', platforms.RAW_FIELDS['reddit'])

    def test_save_manual_fields_auto_injects_over18(self):
        platforms.save_cookies(self.state, 'reddit', {'fields': {'reddit_session': 'fake_session_123'}})
        status = platforms.cookie_status(self.state, 'reddit')
        self.assertTrue(status['present'])
        self.assertTrue(status['usable'])
        self.assertIn('reddit_session', status['names'])
        self.assertIn('over18', status['names'])

        # Verify jar contents
        jar = platforms.load_jar(platforms.cookie_path(self.state, 'reddit'))
        names = {c.name: c.value for c in jar}
        self.assertEqual(names.get('reddit_session'), 'fake_session_123')
        self.assertEqual(names.get('over18'), '1')

    def test_save_cookies_txt_auto_injects_over18(self):
        cookie_text = (
            "# Netscape HTTP Cookie File\n"
            ".reddit.com\tTRUE\t/\tTRUE\t2147483647\ttoken_v2\tsome_token_abc\n"
        )
        platforms.save_cookies(self.state, 'reddit', {'content': cookie_text})
        status = platforms.cookie_status(self.state, 'reddit')
        self.assertTrue(status['present'])
        self.assertTrue(status['usable'])
        self.assertIn('token_v2', status['names'])
        self.assertIn('over18', status['names'])

        jar = platforms.load_jar(platforms.cookie_path(self.state, 'reddit'))
        names = {c.name: c.value for c in jar}
        self.assertEqual(names.get('token_v2'), 'some_token_abc')
        self.assertEqual(names.get('over18'), '1')

    def test_load_jar_existing_file_without_over18(self):
        path = self.state / 'cookies-reddit.txt'
        path.write_text("# Netscape HTTP Cookie File\n.reddit.com\tTRUE\t/\tTRUE\t2147483647\ttoken_v2\txyz\n")
        jar = platforms.load_jar(path)
        names = {c.name: c.value for c in jar}
        self.assertEqual(names.get('over18'), '1')

    def test_redact_reddit_tokens(self):
        text = "Request with reddit_session=SECRET123 and token_v2=SECRET456 in url"
        redacted = platforms.redact(text, self.state)
        self.assertNotIn('SECRET123', redacted)
        self.assertNotIn('SECRET456', redacted)
        self.assertIn('[redacted]', redacted)

    def test_media_build_command_reddit_headers(self):
        job = {
            'id': 'test_job',
            'url': 'https://www.reddit.com/r/test/comments/123/test/',
            'kind': 'video',
            'quality': 'best',
            'playlist': False,
            'container': 'mp4'
        }
        dest = self.state / 'dest'
        dest.mkdir()
        settings = {'platforms': {}, 'playlist_limit': 100, 'media_kib': 0}
        cmd = media.build_command(job, dest, None, settings, self.state)
        self.assertIn('--user-agent', cmd)
        self.assertIn('--add-header', cmd)
        self.assertIn('Referer:https://www.reddit.com/', cmd)

    def test_pick_embed_and_gallery(self):
        # 1. Embed preference
        candidates = [
            {'url': 'https://www.redgifs.com/watch/dummy', 'kind': 'embed'},
            {'url': 'https://v.redd.it/dummy.mp4', 'kind': 'video'}
        ]
        chosen = browser_video.pick(candidates, set())
        self.assertEqual(chosen['kind'], 'embed')
        self.assertEqual(chosen['url'], 'https://www.redgifs.com/watch/dummy')

        # 2. Gallery fallback
        candidates = [
            {'url': 'https://i.redd.it/img1.jpg', 'kind': 'gallery', 'urls': ['https://i.redd.it/img1.jpg', 'https://i.redd.it/img2.jpg']}
        ]
        chosen = browser_video.pick(candidates, set())
        self.assertEqual(chosen['kind'], 'gallery')

    def test_run_reddit_error_does_not_abort_fallback(self):
        spec = {
            'args': ['yt_dlp', '-f', 'best', '--', 'https://www.reddit.com/r/community/comments/123/test/'],
            'url': 'https://www.reddit.com/r/community/comments/123/test/',
            'dest': str(self.state),
            'cookie': '',
            'enabled': True,
            'force': False
        }
        # Simulate yt-dlp error output on Reddit age gate
        reddit_error_output = (
            "WARNING: [Reddit] Session request failed: HTTP Error 403: Blocked\n"
            "ERROR: [Reddit] 123: Account authentication is required. Use --cookies.\n"
        )
        fake_found = {'url': 'https://v.redd.it/stream/DASHPlaylist.mpd', 'kind': 'manifest', 'headers': {}}
        jar = http.cookiejar.MozillaCookieJar()

        with patch('browser_video.execute', return_value=(1, reddit_error_output)), \
             patch('browser_video.profiles.browser_ready', return_value=True), \
             patch('browser_video.detect', return_value=(fake_found, 'Reddit Test Post', jar)) as mock_detect, \
             patch('browser_video.transfer', return_value=0) as mock_transfer:
            res = browser_video.run(spec)
            self.assertEqual(res, 0)
            mock_detect.assert_called_once()
            mock_transfer.assert_called_once()

    def test_transfer_gallery_downloads_images(self):
        spec = {
            'args': ['yt_dlp'],
            'url': 'https://www.reddit.com/r/pics/comments/123/test/',
            'dest': str(self.state),
            'cookie': ''
        }
        fake_bytes = b"fake image content"
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [fake_bytes, b'', fake_bytes, b'']
        mock_opener = MagicMock()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        found = {
            'kind': 'gallery',
            'url': 'https://i.redd.it/pic1.jpg',
            'urls': ['https://i.redd.it/pic1.jpg', 'https://i.redd.it/pic2.png'],
            'headers': {}
        }
        jar = http.cookiejar.MozillaCookieJar()
        with patch('urllib.request.build_opener', return_value=mock_opener), \
             patch('browser_video.validate_url'):
            code = browser_video.transfer(spec, found, 'My Reddit Gallery', jar)
            self.assertEqual(code, 0)
            files = sorted(p.name for p in self.state.iterdir() if p.is_file())
            self.assertIn('My Reddit Gallery_001.jpg', files)
            self.assertIn('My Reddit Gallery_002.png', files)


if __name__ == '__main__':
    unittest.main()
