"""Real browser regression tests with an isolated app, fake sessions and no workers."""
import base64,os,sys,tempfile,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright,expect
from server import Desk,Server,Handler,digest
import platforms
import maintenance

ARTIFACTS=Path('test-artifacts')
def screenshot(page,name):
    if os.getenv('ZEDAIR_REQUIRE_BROWSER')!='1':return
    ARTIFACTS.mkdir(exist_ok=True)
    raw=page.screenshot(path=str(ARTIFACTS/(name+'.jpg')),type='jpeg',quality=55,full_page=False)
    if os.getenv('ZEDAIR_SCREENSHOT_LOGS')=='1':print('ZEDSCREEN '+name+' '+base64.b64encode(raw).decode(),flush=True)
def no_overflow(page):
    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth'), 'Horizontal page overflow'

with sync_playwright() as pw:
    try:browser=pw.chromium.launch(headless=True,chromium_sandbox=True,timeout=15000)
    except Exception as exc:
        print('UNAVAILABLE: sandboxed UI browser: '+str(exc)[:1200])
        raise SystemExit(1 if os.getenv('ZEDAIR_REQUIRE_BROWSER')=='1' else 77)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'ssd/Zedair').mkdir(parents=True)
        desk=Desk({'state':str(root/'state'),'mount':str(root/'ssd'),'require_mount':False,'setup_key_hash':digest('fixture-setup')})
        desk.rpc.call=lambda *a,**k:{'torrents':[]}
        maintenance.UPDATE_STATE=root/'updater';maintenance.UPDATE_STATE.mkdir()
        (maintenance.UPDATE_STATE/'integration.json').write_text('{}')
        # Fixed metadata avoids external release checks in this UI fixture.
        desk.release_cache={'checked_at':time.time(),'packages':{'yt-dlp':{'latest_version':'2099.01.01'},'gallery-dl':{'latest_version':'1.32.19'},'playwright':{'latest_version':'1.65.1'}}};desk.release_checking=False
        platforms.save_cookies(desk.state,'instagram',{'fields':{'sessionid':'fixture-not-a-login'}})
        site=Server(('127.0.0.1',0),Handler);site.desk=desk
        thread=threading.Thread(target=site.serve_forever,daemon=True);thread.start()
        try:
            page=browser.new_page(viewport={'width':1360,'height':940});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            icon_requests=[];failed_icons=set()
            def favicon(route):
                icon_requests.append(route.request.url)
                if route.request.url.split('id=')[-1] in failed_icons:
                    route.fulfill(status=404,body='');return
                route.fulfill(content_type='image/png',body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA9sAAAAASUVORK5CYII='))
            page.route('**/api/favicon?*',favicon)
            page.goto(f'http://127.0.0.1:{site.server_port}')
            page.locator('#authForm [name=setup_key]').fill('fixture-setup')
            page.locator('#authForm [name=username]').fill('fixture')
            page.locator('#authForm [name=password]').fill('fixture-password-long')
            page.locator('#authSubmit').click();expect(page.locator('#shell')).to_be_visible()
            page.locator('[data-open=downloadDialog]').first.click()
            expect(page.locator('#downloadForm [name=container]')).to_have_value('mp4')
            page.locator('#downloadForm [name=urls]').fill('https://files.example.com/photo.jpg\nhttps://example.net/master.m3u8')
            expect(page.locator('#linkCount')).to_have_text('2 links')
            page.locator('#downloadForm [name=urls]').fill('https://files.example.com/photo.jpg')
            expect(page.locator('#linkCount')).to_have_text('1 link')
            page.locator('#downloadForm [name=filename]').fill('My photo.jpg')
            screenshot(page,'download-desktop')
            page.locator('#downloadForm button[type=submit]').click()
            expect(page.locator('#downloadDialog')).not_to_be_visible()
            jobs=desk.rows('SELECT * FROM jobs');assert len(jobs)==1 and jobs[0]['quality']=='best' and jobs[0]['container']=='mp4'
            expect(page.locator('.source-icon')).to_have_class(__import__('re').compile('favicon-loaded'))
            expect(page.locator('.source-fallback')).to_be_hidden()
            page.evaluate('() => { window.fixtureIcon=document.querySelector(".queue-favicon"); }')
            count=len(icon_requests)
            page.evaluate('() => refresh()');page.evaluate('() => refresh()')
            assert page.evaluate('() => window.fixtureIcon === document.querySelector(".queue-favicon")')
            assert len(icon_requests)==count,'Polling refetched favicon'
            desk.update_job(jobs[0]['id'],name='Mountain photographs · original resolution')
            with __import__('unittest.mock').mock.patch('server.validate_url'):
                ids=desk.add_jobs({'urls':['https://video.example.org/watch/42','https://gallery.example.org/post/2'],'quality':'best'})['ids']
            desk.update_job(ids[0],name='Weekend trail · 1080p video',status='running',progress=62)
            failed_icons.add(ids[1])
            desk.update_job(ids[1],name='Architecture collection',status='error',detail='HTTP 403: the website rejected the saved session.')
            desk.live[ids[0]]={'speed':2457600,'eta':32,'progress':62}
            desk.record_usage(jobs[0]['id'],83*1024**2);desk.record_usage(ids[0],914*1024**2)
            page.evaluate('() => refresh()')
            expect(page.locator('#aggregateSpeed')).to_be_hidden()
            expect(page.locator('[data-progress-job="'+ids[1]+'"] .source-fallback')).to_be_visible()
            desk.update_job(jobs[0]['id'],status='running',progress=24)
            desk.live[jobs[0]['id']]={'speed':524288,'eta':60,'progress':24}
            page.evaluate('() => refresh()')
            expect(page.locator('#aggregateSpeed')).to_be_visible()
            expect(page.locator('#aggregateSpeed')).to_contain_text('2.8 MiB/s')
            no_overflow(page);screenshot(page,'queue-desktop')
            page.locator('[data-page=settings]').click()
            slider=page.locator('#settingsForm [name=simultaneous_downloads]')
            expect(slider).to_have_attribute('min','1');expect(slider).to_have_attribute('max','20');expect(slider).to_have_attribute('step','1')
            slider.focus();slider.press('Home');slider.press('ArrowRight');slider.press('ArrowRight')
            expect(page.locator('#settingsSaveStatus')).to_have_text('Saved automatically')
            assert desk.get_setting('simultaneous_downloads')==3
            screenshot(page,'settings-desktop')
            page.reload();expect(page.locator('#shell')).to_be_visible()
            page.locator('[data-page=settings]').click()
            expect(page.locator('#settingsForm [name=simultaneous_downloads]')).to_have_value('3')
            page.get_by_text('Telegram connection & diagnostics',exact=True).click()
            page.locator('#settingsForm [name=telegram_bot_token]').fill('123:fixture')
            page.locator('#settingsForm button.primary').click()
            expect(page.locator('#toast')).to_contain_text('Connection settings saved')
            assert desk.get_setting('telegram_bot_token')=='123:fixture'
            expect(page.locator('#settingsForm [name=telegram_bot_token]')).to_have_value('')
            page.locator('[data-settings-tab=engines]').click()
            expect(page.locator('#packageInventory')).to_contain_text('yt-dlp')
            expect(page.locator('#packageInventory')).to_contain_text('New release available')
            expect(page.locator('[data-update-package]')).to_have_count(1)
            expect(page.locator('[data-update-package]')).to_have_attribute('data-update-package','yt-dlp')
            expect(page.locator('#packageInventory')).not_to_contain_text('Not installed · Latest 1.32.19')
            screenshot(page,'engines-desktop')
            page.locator('[data-settings-tab=usage]').click()
            expect(page.locator('#usageReport svg')).to_be_visible()
            expect(page.locator('#usageReport')).to_contain_text('files.example.com')
            screenshot(page,'usage-desktop')
            page.locator('[data-page=tools]').click()
            page.locator('#storiesForm [name=profile]').fill('https://www.instagram.com/s/aGlnaGxpZ2h0OjE3OTY1ODUwMDI4MTY4NjAx/')
            page.locator('#storiesForm [name=mode]').select_option('highlights')
            page.locator('#storiesForm button[type=submit]').click()
            expect(page.locator('#toast')).to_contain_text('Collection queued')
            stories=desk.rows("SELECT * FROM jobs WHERE profile_mode='highlights'")
            assert len(stories)==1 and stories[0]['url'].endswith('/stories/highlights/17965850028168601/')
            page.locator('[data-page=queue]').click()
            # Backdrop click closes; clicking inside does not.
            page.locator('[data-open=downloadDialog]').first.click()
            page.locator('#downloadForm h2').click();expect(page.locator('#downloadDialog')).to_be_visible()
            page.mouse.click(5,5);expect(page.locator('#downloadDialog')).not_to_be_visible()
            page.locator('#themeBtn').click()
            page.set_viewport_size({'width':390,'height':844});page.evaluate('() => scrollTo(0,0)')
            no_overflow(page);screenshot(page,'queue-mobile-dark')
            page.locator('[data-open=downloadDialog]').first.click();screenshot(page,'download-mobile-dark')
            no_overflow(page);page.mouse.click(2,2);expect(page.locator('#downloadDialog')).not_to_be_visible()
            page.locator('[data-page=settings]').click();page.locator('[data-settings-tab=general]').click()
            page.evaluate('() => scrollTo(0,0)');no_overflow(page);screenshot(page,'settings-mobile-dark')
            page.route('**/api/system',lambda r:r.fulfill(json={'cpu_percent':95,'cores':4,'memory':{'total':2147483648,'used':1610612736,'available':536870912,'percent':75},'swap':{'total':0,'used':0,'percent':0},'system_disk':{'total':32000000000,'used':6400000000,'percent':20},'ssd':None,'temperature_c':83,'uptime_seconds':3600,'load':[1,1,1],'sampled_at':time.time()}))
            page.locator('[data-settings-tab=system]').click()
            expect(page.locator('.usage-ring[data-level=critical]')).to_have_count(1)
            expect(page.locator('.usage-ring[data-level=warning]')).to_have_count(1)
            page.evaluate('() => scrollTo(0,0)');no_overflow(page);screenshot(page,'system-mobile-dark')
            page.route('**/api/state',lambda r:r.abort())
            page.evaluate('() => refresh()');expect(page.locator('#connection')).to_have_class(__import__('re').compile('offline'))
            assert page.locator('#connection').evaluate('(el) => getComputedStyle(el).color')=='rgb(255, 143, 167)'
            page.unroute('**/api/state');page.evaluate('() => refresh()')
            expect(page.locator('#connection')).not_to_have_class(__import__('re').compile('offline'))
            assert page.locator('#connection').evaluate('(el) => getComputedStyle(el).color')=='rgb(105, 215, 165)'
            assert not errors,errors
            print('PASS: real UI verified default MP4, live link count, stable single favicon, range autosave 1–20 step 1, masked token, release-aware engine cards, usage chart, exact highlight share, backdrop dismissal and mobile/dark layouts without overflow.')
        finally:
            browser.close();site.shutdown();site.server_close();thread.join();desk.close()
