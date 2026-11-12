"""Browser/API smoke test of new queue, settings and Instagram Tools flows.
Uses a temporary application, no workers, fake credentials and no live downloads.
"""
import os,sys,tempfile,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright,expect
from server import Desk,Server,Handler,digest
import platforms

with sync_playwright() as pw:
    try:browser=pw.chromium.launch(headless=True,chromium_sandbox=True,timeout=15000)
    except Exception as exc:
        print('UNAVAILABLE: sandboxed UI browser: '+str(exc)[:1200])
        raise SystemExit(1 if os.getenv('ZEDAIR_REQUIRE_BROWSER')=='1' else 77)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'ssd/Zedair').mkdir(parents=True)
        desk=Desk({'state':str(root/'state'),'mount':str(root/'ssd'),'require_mount':False,'setup_key_hash':digest('fixture-setup')})
        desk.rpc.call=lambda *a,**k:{'torrents':[]}
        platforms.save_cookies(desk.state,'instagram',{'fields':{'sessionid':'fixture-not-a-login'}})
        site=Server(('127.0.0.1',0),Handler);site.desk=desk
        thread=threading.Thread(target=site.serve_forever,daemon=True);thread.start()
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{site.server_port}')
            page.locator('#authForm [name=setup_key]').fill('fixture-setup')
            page.locator('#authForm [name=username]').fill('fixture')
            page.locator('#authForm [name=password]').fill('fixture-password-long')
            page.locator('#authSubmit').click();expect(page.locator('#shell')).to_be_visible()
            page.locator('[data-open=downloadDialog]').first.click()
            page.locator('#downloadForm [name=urls]').fill('https://files.example.com/photo.jpg')
            page.locator('#downloadForm [name=filename]').fill('My photo.jpg')
            page.locator('#downloadForm [name=container]').select_option('mp4')
            page.locator('#downloadForm button[type=submit]').click()
            expect(page.locator('#downloadDialog')).not_to_be_visible()
            jobs=desk.rows('SELECT * FROM jobs');assert len(jobs)==1 and jobs[0]['quality']=='best' and jobs[0]['container']=='mp4'
            page.locator('[data-page=settings]').click()
            page.locator('#settingsForm [name=simultaneous_downloads]').fill('3')
            page.get_by_text('Telegram diagnostics',exact=True).click()
            page.locator('#settingsForm [name=telegram_bot_token]').fill('123:fixture')
            page.locator('#settingsForm button.primary').click()
            expect(page.locator('#toast')).to_contain_text('Settings saved')
            assert desk.get_setting('simultaneous_downloads')==3
            assert desk.get_setting('telegram_bot_token')=='123:fixture'
            page.locator('[data-settings-tab=engines]').click()
            expect(page.locator('#packageInventory')).to_contain_text('yt-dlp')
            page.locator('[data-page=tools]').click()
            page.locator('#storiesForm [name=profile]').fill('fixture')
            page.locator('#storiesForm button[type=submit]').click()
            expect(page.locator('#toast')).to_contain_text('Collection queued')
            stories=desk.rows("SELECT * FROM jobs WHERE profile_mode='stories'")
            assert len(stories)==1 and stories[0]['subdir']=='Instagram/fixture'
            assert not errors,errors
            print('PASS: actual browser completed setup, queued highest-quality/MP4 image request, saved concurrency and masked-token settings, loaded package inventory and queued Instagram Stories in its separate Tools form; no JavaScript errors.')
        finally:
            browser.close();site.shutdown();site.server_close();thread.join();desk.close()
