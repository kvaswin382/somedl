#!/usr/bin/env python3
"""Zedair Reddit/Telegram self-test. Run on the laptop:

  sudo runuser -u zedair -- /opt/zedair/venv/bin/python /opt/zedair/reddit_diag.py 'REDDIT_POST_URL'

Prints a step-by-step report (no cookie values). Paste the output when asking for help.
"""
import json, os, sqlite3, sys, time, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
STATE = Path('/var/lib/zedair')

def step(name):
    print('\n== ' + name, flush=True)

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else ''
    import platforms, reddit_media as rm, profiles, telegram_notifier as tn
    step('Versions')
    print(sys.version.split()[0], 'user=' + os.environ.get('USER', str(os.getuid())))
    step('Reddit cookie file')
    status = platforms.cookie_status(STATE, 'reddit')
    print({k: v for k, v in status.items()})
    cookie = STATE / 'cookies-reddit.txt'
    step('Telegram settings / connectivity')
    try:
        db = sqlite3.connect(STATE / 'desk.sqlite3')
        cfg = {k: json.loads(v) for k, v in db.execute("SELECT key,value FROM settings WHERE key LIKE 'telegram_%'")}
        token, chat, admin = cfg.get('telegram_bot_token', ''), cfg.get('telegram_chat_id', ''), cfg.get('telegram_admin_id', '')
        print('token set:', bool(token), '| chat id:', chat or '(empty)', '| admin id:', admin or '(empty)', '| snapshots on:', cfg.get('telegram_snapshots'))
        r = tn.send_message(token, [chat, admin], 'Zedair diagnostic: text OK')
        print('sendMessage:', r)
        r = tn.send_snapshot(token, [chat, admin], tn.test_image(), 'Zedair diagnostic: photo OK', mime='image/png', filename='t.png')
        print('sendPhoto:', r)
    except Exception as exc:
        print('Telegram check failed:', type(exc).__name__, exc)
    if not url:
        print('\n(no URL given; skipping Reddit steps)'); return
    step('Browser engine')
    print('browser_ready:', profiles.browser_ready())
    step('Direct Reddit fetch (urllib)')
    import http.cookiejar
    jar = platforms.load_jar(cookie) if cookie.exists() else http.cookiejar.MozillaCookieJar()
    opener = rm.make_opener(jar)
    final = url
    try:
        final = rm.resolve_share_link(opener, url)
        print('resolved link:', final)
    except Exception as exc:
        print('share-link resolve failed:', exc)
    ident = rm.post_id_and_path(final)[0]
    print('post id:', ident)
    for label, ep in (('www', f'https://www.reddit.com/comments/{ident}.json?raw_json=1&limit=1'),
                      ('old', f'https://old.reddit.com/comments/{ident}.json?raw_json=1&limit=1')):
        t = time.time()
        try:
            raw, _ = rm.fetch(opener, ep)
            post = rm.find_post(json.loads(raw.decode('utf-8', 'replace')))
            print(label, 'HTTP 200, post found:', bool(post), '| over_18:', post and post.get('over_18'), f'({time.time()-t:.1f}s)')
            if post:
                print('classified as:', {k: v for k, v in rm.classify(post).items() if k in ('kind', 'dash', 'fallback', 'url', 'height')})
        except urllib.error.HTTPError as exc:
            print(label, 'HTTP', exc.code, exc.read(160))
        except Exception as exc:
            print(label, type(exc).__name__, str(exc)[:200])
    step('Browser fetch (Chromium)')
    t = time.time()
    try:
        post = rm.browser_post(ident, jar)
        print('post found; over_18:', post.get('over_18'), f'({time.time()-t:.1f}s)')
    except Exception as exc:
        print('browser fetch failed:', str(exc)[:400])
    step('Full resolver')
    try:
        info, _, _ = rm.resolve(final, str(cookie) if cookie.exists() else '')
        print({k: v for k, v in info.items() if k != 'urls'})
    except Exception as exc:
        print('resolve failed:', str(exc)[:500])
    print('\nDone.')

if __name__ == '__main__':
    main()
