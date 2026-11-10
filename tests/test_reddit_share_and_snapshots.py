"""Reddit share links, HTML/HLS fallbacks, browser-session post reads and Telegram delivery."""
import http.cookiejar
import http.server
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch

import browser_video
import platforms
import reddit_media


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {}
    seen_cookie = ''

    def do_GET(self):
        _Handler.seen_cookie = self.headers.get('Cookie', '')
        status, headers, body = self.routes.get(self.path, (404, {}, b'missing'))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LocalServer:
    def __enter__(self):
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self.base = f'http://127.0.0.1:{self.httpd.server_port}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class ShareLinkTests(unittest.TestCase):
    def setUp(self):
        # The SSRF guard rejects 127.0.0.1; these tests exercise redirect handling only.
        self.no_ssrf = patch('reddit_media.media.validate_url', side_effect=lambda u, dns=True: u)
        self.no_ssrf.start()

    def tearDown(self):
        self.no_ssrf.stop()
        _Handler.routes = {}

    def test_share_link_follows_location_without_reading_large_page(self):
        with LocalServer() as srv:
            _Handler.routes = {
                '/r/sub/s/xJH9JJUPgc': (301, {'Location': '/r/sub/comments/abc123/some_title/?share_id=x'}, b''),
                '/r/sub/comments/abc123/some_title/?share_id=x': (200, {'Content-Type': 'text/html'}, b'x' * 500_000),
            }
            final = reddit_media.resolve_share_link(http.cookiejar.CookieJar(), srv.base + '/r/sub/s/xJH9JJUPgc')
        self.assertTrue(final.endswith('/r/sub/comments/abc123/some_title/?share_id=x'))
        self.assertEqual(reddit_media.post_id_and_path(final)[0], 'abc123')

    def test_share_link_sends_session_cookie_and_reads_over18_dest(self):
        with LocalServer() as srv:
            _Handler.routes = {'/r/sub/s/abc': (302, {'Location': '/over18?dest=https%3A%2F%2Fwww.reddit.com%2Fr%2Fsub%2Fcomments%2Fq1w2e3%2Ft%2F'}, b'')}
            jar = http.cookiejar.CookieJar()
            jar.set_cookie(http.cookiejar.Cookie(0, 'reddit_session', 'secret', None, False, '127.0.0.1', False, False,
                                                 '/', True, False, None, True, None, None, {}, False))
            final = reddit_media.resolve_share_link(jar, srv.base + '/r/sub/s/abc')
        self.assertEqual(final, 'https://www.reddit.com/r/sub/comments/q1w2e3/t/')
        self.assertIn('reddit_session=secret', _Handler.seen_cookie)

    def test_share_link_ending_on_subreddit_is_a_clear_error(self):
        with LocalServer() as srv:
            _Handler.routes = {'/r/sub/s/abc': (307, {'Location': '/r/sub/'}, b'')}
            with self.assertRaisesRegex(ValueError, 'did not lead to a post'):
                reddit_media.resolve_share_link(http.cookiejar.CookieJar(), srv.base + '/r/sub/s/abc')
        self.assertEqual(platforms.failure_kind('Reddit share link did not lead to a post (ended at /r/sub/)')[0], 'unsupported')

    def test_regular_permalink_is_untouched(self):
        url = 'https://www.reddit.com/r/sub/comments/abc123/t/'
        self.assertEqual(reddit_media.resolve_share_link(http.cookiejar.CookieJar(), url), url)


class FallbackTests(unittest.TestCase):
    def test_post_from_html_reads_shreddit_post(self):
        page = ('<html><shreddit-post id="t3_abc123" post-title="Nice &amp; clip" nsfw="" '
                'content-href="https://v.redd.it/vid987"></shreddit-post>'
                '<shreddit-player-2 src="https://v.redd.it/vid987/HLSPlaylist.m3u8?a=1&amp;v=1&amp;f=sd"></shreddit-player-2>')
        with patch('reddit_media.fetch', return_value=(page.encode(), 'https://www.reddit.com/comments/abc123/')):
            post = reddit_media.post_from_html(None, 'abc123')
        info = reddit_media.classify(post)
        self.assertEqual(info['kind'], 'video')
        self.assertEqual(info['title'], 'Nice & clip')
        self.assertTrue(info['over_18'])
        self.assertEqual(info['dash'], 'https://v.redd.it/vid987/DASHPlaylist.mpd')
        self.assertEqual(info['hls'], 'https://v.redd.it/vid987/HLSPlaylist.m3u8?a=1&v=1&f=sd')

    def test_load_post_uses_html_when_json_is_blocked(self):
        page = b'<shreddit-post id="t3_abc123" post-title="T" content-href="https://v.redd.it/vid987"></shreddit-post>'

        def fake_fetch(opener, url, headers=None, **kw):
            if '.json' in url:
                raise urllib.error.HTTPError(url, 403, 'Blocked', {}, None)
            return page, url
        with patch('reddit_media.fetch', side_effect=fake_fetch):
            post, _ = reddit_media.load_post(None, http.cookiejar.CookieJar(), 'https://www.reddit.com/comments/abc123/')
        self.assertEqual(post['url'], 'https://v.redd.it/vid987')

    def test_run_falls_back_to_hls_when_dash_fails(self):
        info = {'kind': 'video', 'title': 'T', 'id': 'abc', 'dash': 'https://v.redd.it/x/DASHPlaylist.mpd',
                'hls': 'https://v.redd.it/x/HLSPlaylist.m3u8', 'fallback': '', 'height': 0}
        with patch('reddit_media.resolve', return_value=(info, None, None)), \
                patch('reddit_media.download_video', side_effect=ValueError('No usable Reddit video URL')):
            self.assertEqual(reddit_media.run('https://www.reddit.com/comments/abc/', '/tmp', ''),
                             ('external', 'https://v.redd.it/x/HLSPlaylist.m3u8', 'T'))

    def test_cmaf_fallback_url(self):
        info = {'kind': 'video', 'title': 'T', 'id': 'abc', 'dash': '', 'hls': '',
                'fallback': 'https://v.redd.it/x/CMAF_1080.mp4?source=fallback', 'height': 0}
        with tempfile.TemporaryDirectory() as tmp, \
                patch('reddit_media.audio_guess', return_value=None), \
                patch('reddit_media.download_file') as dl, \
                patch('reddit_media.os.replace'):
            reddit_media.download_video(None, info, tmp, 0, 0)
        self.assertEqual(dl.call_args_list[0][0][1], 'https://v.redd.it/x/CMAF_1080.mp4')


class EngineHandoffTests(unittest.TestCase):
    def test_share_link_is_rewritten_for_yt_dlp(self):
        spec = {'args': ['python', '-m', 'yt_dlp', '-f', 'bv*+ba/b', '--', 'https://www.reddit.com/r/sub/s/xJH9JJUPgc'],
                'url': 'https://www.reddit.com/r/sub/s/xJH9JJUPgc', 'dest': '/tmp', 'cookie': '', 'enabled': True}
        with patch('reddit_media.resolve_share_link', return_value='https://www.reddit.com/r/sub/comments/abc123/t/'):
            browser_video.resolve_reddit_url(spec)
        self.assertEqual(spec['url'], 'https://www.reddit.com/r/sub/comments/abc123/t/')
        self.assertEqual(spec['args'][-1], 'https://www.reddit.com/r/sub/comments/abc123/t/')

    def test_browser_reddit_post_is_preferred_and_downloaded_directly(self):
        info = {'kind': 'video', 'title': 'Clip', 'id': 'abc', 'dash': 'https://v.redd.it/x/DASHPlaylist.mpd',
                'hls': '', 'fallback': '', 'height': 0}
        found = {'kind': 'reddit_post', 'url': 'https://www.reddit.com/comments/abc/', 'info': info, 'headers': {}}
        self.assertIs(browser_video.pick([{'url': 'https://x/HLSPlaylist.m3u8', 'kind': 'manifest'}, found], set()), found)
        spec = {'args': ['yt_dlp', '-f', 'bv*[height<=720]+ba', '--', 'u'], 'url': 'u', 'dest': '/tmp'}
        with patch('reddit_media.download_video') as dv:
            self.assertEqual(browser_video.transfer(spec, found, '', http.cookiejar.MozillaCookieJar()), 0)
        self.assertEqual(dv.call_args[0][3], 720)

    def test_browser_reddit_post_uses_hls_when_dash_fails(self):
        info = {'kind': 'video', 'title': 'Clip', 'id': 'abc', 'dash': 'd', 'hls': 'https://v.redd.it/x/HLSPlaylist.m3u8',
                'fallback': '', 'height': 0}
        found = {'kind': 'reddit_post', 'url': 'u', 'info': info, 'headers': {}}
        with tempfile.TemporaryDirectory() as tmp:
            spec = {'args': ['yt_dlp', '-f', 'b', '-o', 'x', '--', 'u'], 'url': 'https://www.reddit.com/comments/abc/', 'dest': tmp}
            with patch('reddit_media.download_video', side_effect=ValueError('DASH: 403')), \
                    patch('browser_video.execute', return_value=(0, '')) as ex:
                self.assertEqual(browser_video.transfer(spec, found, '', http.cookiejar.MozillaCookieJar()), 0)
        self.assertEqual(ex.call_args[0][0][-1], 'https://v.redd.it/x/HLSPlaylist.m3u8')

    def test_no_media_is_not_retried_forever(self):
        self.assertEqual(platforms.failure_kind('ERROR Browser fallback: No unambiguous playable media found.')[0], 'unsupported')


class TelegramLogTests(unittest.TestCase):
    def test_deliveries_are_waited_for_before_exit(self):
        sent = []

        def slow_send(token, recipients, text):
            time.sleep(0.3)
            sent.append(text)
            return {'ok': True, 'successes': recipients, 'errors': []}
        tg = browser_video.TelegramLog({'bot_token': 't', 'chat_id': '-100', 'admin_id': '5', 'snapshots': True})
        with patch.object(tg.notifier, 'send_message', side_effect=slow_send):
            tg.text('started')
            tg.flush(5)
        self.assertEqual(sent, ['started'])

    def test_disabled_without_token(self):
        tg = browser_video.TelegramLog({'bot_token': '', 'chat_id': '-100', 'snapshots': True})
        self.assertFalse(tg.enabled)
        tg.text('nothing')
        self.assertEqual(tg.pending, [])


if __name__ == '__main__':
    unittest.main()
