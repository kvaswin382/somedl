"""Regression coverage for session cookies written back by gallery-dl."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import platforms
from test_desk import Fixture


def exported(session='fixture-session', csrf='fixture-csrf', expiry='0'):
    return ('# Netscape HTTP Cookie File\n'
            f'.instagram.com\tTRUE\t/\tTRUE\t{expiry}\tsessionid\t{session}\n'
            f'.instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\t{csrf}\n')


class InstagramSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        platforms.save_cookies(self.state, 'instagram', {'fields': {'sessionid': 'fixture-session'}})
        self.path = self.state/'cookies-instagram.txt'

    def tearDown(self):
        self.tmp.cleanup()

    def test_existing_gallery_export_zero_expiry_is_usable_without_reimport(self):
        self.path.write_text(exported())
        status = platforms.cookie_status(self.state, 'instagram')
        self.assertTrue(status['usable'])
        self.assertEqual(status['expired'], 0)
        self.assertTrue(all(c.expires is None for c in platforms.load_jar(self.path)))

    def test_consecutive_jobs_keep_rotated_session_and_csrf(self):
        for index in range(3):
            session = platforms.InstagramSession(self.path)
            self.assertEqual(session.path.stat().st_mode & 0o777, 0o600)
            jar = {c.name: c.value for c in platforms.load_jar(session.path)}
            self.assertEqual(jar['sessionid'], 'fixture-session' if index == 0 else f'rotated-{index-1}')
            session.path.write_text(exported(f'rotated-{index}', f'csrf-{index}'))
            self.assertTrue(session.commit())
            session.close()
            self.assertTrue(platforms.cookie_status(self.state, 'instagram')['usable'])
        saved = {c.name: c.value for c in platforms.load_jar(self.path)}
        self.assertEqual(saved['csrftoken'], 'csrf-2')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.state.glob('cookies-work-*')))

    def test_failed_job_does_not_replace_saved_session(self):
        original = self.path.read_bytes()
        session = platforms.InstagramSession(self.path)
        session.path.write_text('# Netscape HTTP Cookie File\n')
        self.assertFalse(session.commit())
        session.close()
        self.assertEqual(self.path.read_bytes(), original)

    def test_explicit_past_expiry_is_not_extended(self):
        session = platforms.InstagramSession(self.path)
        session.path.write_text(exported(expiry='1'))
        self.assertFalse(session.commit())
        session.close()
        self.path.write_text(exported(expiry='1'))
        self.assertFalse(platforms.cookie_status(self.state, 'instagram')['usable'])

    def test_gui_replacement_and_deletion_win_over_active_job(self):
        for delete in (False, True):
            session = platforms.InstagramSession(self.path)
            if delete:
                platforms.save_cookies(self.state, 'instagram', {'delete': True})
            else:
                platforms.save_cookies(self.state, 'instagram', {'fields': {'sessionid': 'new-from-gui'}})
            session.path.write_text(exported('old-job-rotation'))
            self.assertFalse(session.commit())
            session.close()
            if delete:
                self.assertFalse(self.path.exists())
            else:
                self.assertEqual(next(iter(platforms.load_jar(self.path))).value, 'new-from-gui')

    def test_refresh_filters_domains_and_redacts_working_jar(self):
        session = platforms.InstagramSession(self.path)
        session.path.write_text(exported('rotated-secret')+'.example.com\tTRUE\t/\tTRUE\t0\tother\tother-secret\n')
        self.assertNotIn('rotated-secret', platforms.redact('rotated-secret', self.state))
        self.assertTrue(session.commit())
        self.assertTrue(all(c.domain == '.instagram.com' for c in platforms.load_jar(self.path)))

    def test_error_messages_distinguish_challenges_and_limits(self):
        for message, kind in [('challenge_required', 'challenge'), ('checkpoint_required','challenge'),
                              ('HTTP Error 429','rate_limit'), ('login_required','auth')]:
            category, detail = platforms.failure_kind(message)
            self.assertEqual(category, kind)
            self.assertNotIn('replace the session cookies', detail)


class InstagramWorkerTests(Fixture):
    def test_worker_uses_private_cookie_copy_and_keeps_refreshed_session(self):
        platforms.save_cookies(self.desk.state,'instagram',{'fields':{'sessionid':'original-fixture'}})
        source = self.desk.state/'cookies-instagram.txt'
        for i in range(2):
            jid = self.create_job(f'https://instagram.com/p/test{i}/')
            def command(job, dest, cookie, settings, state):
                self.assertNotEqual(cookie, source)
                self.assertEqual(next(c.value for c in platforms.load_jar(cookie) if c.name=='sessionid'),
                                 'original-fixture' if i==0 else 'refreshed-0')
                program = ('from pathlib import Path; '
                           f'Path({str(cookie)!r}).write_text({exported(f"refreshed-{i}")!r}); '
                           f'Path({str(dest/"fixture.jpg")!r}).write_bytes(b"fixture image")')
                return [sys.executable, '-c', program]
            with patch('server.validate_url'), patch('server.build_command', side_effect=command):
                self.desk.run_job(self.desk.job(jid))
            self.assertEqual(self.desk.job(jid)['status'], 'done', self.desk.job(jid)['last_error'])
            self.assertTrue(platforms.cookie_status(self.desk.state,'instagram')['usable'])
            self.assertFalse(list(self.desk.state.glob('cookies-work-*')))
