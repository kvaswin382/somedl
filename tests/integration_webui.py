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
    page.locator('#toast').evaluate('(el) => { el.hidden=true; }')
    raw=page.screenshot(path=str(ARTIFACTS/(name+'.jpg')),type='jpeg',quality=55,full_page=False,animations='disabled')
    if os.getenv('ZEDAIR_SCREENSHOT_LOGS')=='1':print('ZEDSCREEN '+name+' '+base64.b64encode(raw).decode(),flush=True)
def no_overflow(page):
    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth'), 'Horizontal page overflow'

def original_theme(page,dark=False):
    expected={'--blue':'#425eac','--paper':'#17181b' if dark else '#f3f4f6',
              '--surface':'#202125' if dark else '#fff','--soft':'#292b30' if dark else '#eaecf0',
              '--line':'#383b43' if dark else '#dadde3','--ink':'#f2f3f5' if dark else '#16201e',
              '--muted':'#a5a9b4' if dark else '#6c726c'}
    actual=page.evaluate('(keys) => Object.fromEntries(keys.map(k => [k,getComputedStyle(document.body).getPropertyValue(k).trim().toLowerCase()]))',list(expected))
    assert actual==expected,(actual,expected)

def centered_settings(page):
    geometry=page.evaluate("""() => {
        const area=document.querySelector('.workspace').getBoundingClientRect();
        const panel=document.querySelector('#settingsPage').getBoundingClientRect();
        return {center:Math.abs((area.left+area.right)-(panel.left+panel.right)),
            panels:[...document.querySelectorAll('[data-settings-panel]:not([hidden])')].map(el=>{
                const r=el.getBoundingClientRect();return Math.abs((r.left+r.right)-(panel.left+panel.right));})};
    }""")
    assert geometry['center']<2 and all(n<2 for n in geometry['panels']),geometry

with sync_playwright() as pw:
    try:browser=pw.chromium.launch(headless=True,chromium_sandbox=True,timeout=15000)
    except Exception as exc:
        print('UNAVAILABLE: sandboxed UI browser: '+str(exc)[:1200])
        raise SystemExit(1 if os.getenv('ZEDAIR_REQUIRE_BROWSER')=='1' else 77)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'ssd/Zedair/Travel photos').mkdir(parents=True)
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
            expect(page.locator('#appVersion')).to_have_text('v2.1.3')
            original_theme(page)
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
            # Full state and fast polling must show the same byte/rate ETA.
            import io,json
            jid=ids[0];saved=dict(desk.live[jid])
            desk.consume(io.BytesIO(('ZEDPROG '+json.dumps({'downloaded_bytes':40*1024**2,'total_bytes':100*1024**2,'speed':1024**2,'eta':9000,'filename':'fixture.mp4'})+'\n').encode()),jid)
            page.evaluate('() => refresh()')
            expect(page.locator('[data-progress-job="'+jid+'"] .job-speed')).to_contain_text('1.0 MiB/s · ETA ≈ 1m 0s · current file')
            fast=page.evaluate('() => api("progress")')
            row=next(j for j in fast['jobs'] if j['id']==jid)
            assert row['eta']==60 and row['speed']==1024**2
            desk.live[jid]['progress_at']-=11
            fast=page.evaluate('() => api("progress")')
            row=next(j for j in fast['jobs'] if j['id']==jid)
            assert row['eta'] is None and row['speed']==0 and row['transfer_state']=='waiting'
            page.evaluate('(j)=>{Object.assign(state.jobs.find(x=>x.id===j.id),j);renderJobs(true);}',row)
            expect(page.locator('[data-progress-job="'+jid+'"] .job-speed')).to_have_text('Waiting for data · ETA —')
            desk.consume(io.BytesIO(b'ZEDPROG {"downloaded_bytes":104857600,"total_bytes":104857600,"speed":999999,"eta":999,"status":"finished"}\n'),jid)
            page.evaluate('() => refresh()')
            expect(page.locator('[data-progress-job="'+jid+'"] .job-speed')).to_have_text('Merging / finishing · ETA —')
            assert page.evaluate('() => [eta(0),eta(61),eta(3661),eta(90000),eta(null),eta(Infinity)]')==['0s','1m 1s','1h 1m','1d 1h','—','—']
            desk.live[jid]=saved;page.evaluate('() => refresh()')
            no_overflow(page);screenshot(page,'queue-desktop')
            page.locator('[data-page=settings]').click()
            slider=page.locator('#settingsForm [name=simultaneous_downloads]')
            expect(slider).to_have_attribute('min','1');expect(slider).to_have_attribute('max','20');expect(slider).to_have_attribute('step','1')
            slider.focus();slider.press('Home');slider.press('ArrowRight');slider.press('ArrowRight')
            expect(page.locator('#settingsSaveStatus')).to_have_text('Saved automatically')
            assert desk.get_setting('simultaneous_downloads')==3
            assert slider.evaluate('(el) => getComputedStyle(el).outlineStyle')=='none'
            # Pointer use must not add the old input-focus rectangle either.
            slider.click();expect(page.locator('#settingsSaveStatus')).to_have_text('Saved automatically')
            assert slider.evaluate('(el) => getComputedStyle(el).outlineStyle')=='none'
            slider.press('Home');slider.press('ArrowRight');slider.press('ArrowRight')
            expect(page.locator('#settingsSaveStatus')).to_have_text('Saved automatically')
            page.set_viewport_size({'width':1680,'height':1000});centered_settings(page)
            screenshot(page,'settings-desktop')
            page.locator('#themeBtn').click();original_theme(page,True)
            assert page.locator('#value-simultaneous_downloads').evaluate('(el) => getComputedStyle(el).color')=='rgb(242, 243, 245)'
            assert page.locator('[data-settings-tab=general]').evaluate('(el) => getComputedStyle(el).color')=='rgb(32, 33, 37)'
            screenshot(page,'settings-desktop-dark')
            page.locator('#themeBtn').click()
            # Deliberately open Settings before the initial state arrives.
            pending=[];page.route('**/api/state',lambda route:pending.append(route))
            page.reload();expect(page.locator('#shell')).to_be_visible()
            page.locator('[data-page=settings]').click()
            assert pending, 'Initial status request was not intercepted'
            pending[-1].fulfill(json=desk.snapshot());page.unroute('**/api/state')
            expect(page.locator('#settingsForm [name=simultaneous_downloads]')).to_have_value('3')
            page.get_by_text('Telegram connection & diagnostics',exact=True).click()
            page.locator('#settingsForm [name=telegram_bot_token]').fill('123:fixture')
            page.locator('#settingsForm button.primary').click()
            expect(page.locator('#toast')).to_contain_text('Connection settings saved')
            assert desk.get_setting('telegram_bot_token')=='123:fixture'
            expect(page.locator('#settingsForm [name=telegram_bot_token]')).to_have_value('')
            # Test uses the fields currently visible, even without a separate Save click.
            page.locator('#settingsForm [name=telegram_bot_token]').fill('456:new_fixture')
            page.locator('#settingsForm [name=telegram_chat_id]').fill('-1001234')
            page.locator('#telegramTest').click()
            expect(page.locator('#toast')).to_contain_text('Connection saved; test queued')
            assert desk.get_setting('telegram_bot_token')=='456:new_fixture'
            assert desk.get_setting('telegram_chat_id')=='-1001234'
            assert not desk.get_setting('telegram_errors') and not desk.get_setting('telegram_snapshots')
            assert desk.notices.get_nowait()==('Zedair test notification',None)
            assert desk.critical_notices.empty(), 'General test must not send to admin'
            page.locator('[data-settings-tab=engines]').click()
            expect(page.locator('#packageInventory')).to_contain_text('yt-dlp')
            expect(page.locator('#packageInventory')).to_contain_text('New release available')
            expect(page.locator('[data-update-package]')).to_have_count(1)
            expect(page.locator('[data-update-package]')).to_have_attribute('data-update-package','yt-dlp')
            expect(page.locator('#packageInventory')).not_to_contain_text('Not installed · Latest 1.32.19')
            centered_settings(page)
            expect(page.locator('.engine-table tbody tr')).to_have_count(3)
            assert all(row.bounding_box()['height']<90 for row in page.locator('.engine-table tbody tr').all())
            screenshot(page,'engines-desktop')
            page.locator('#themeBtn').click();original_theme(page,True);screenshot(page,'engines-desktop-dark')
            page.locator('#themeBtn').click()
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
            assert page.locator('#jobs .job').first.bounding_box()['y']<800, 'Queue is pushed below the mobile viewport'
            page.locator('[data-open=downloadDialog]').first.click();screenshot(page,'download-mobile-dark')
            no_overflow(page);page.mouse.click(2,2);expect(page.locator('#downloadDialog')).not_to_be_visible()
            page.locator('[data-page=settings]').click();page.locator('[data-settings-tab=general]').click()
            page.evaluate('() => scrollTo(0,0)');no_overflow(page);centered_settings(page);original_theme(page,True);screenshot(page,'settings-mobile-dark')
            page.locator('[data-settings-tab=engines]').click()
            expect(page.locator('.engine-table tbody tr')).to_have_count(3)
            page.evaluate('() => scrollTo(0,0)');no_overflow(page);centered_settings(page);screenshot(page,'engines-mobile-dark')
            page.route('**/api/system',lambda r:r.fulfill(json={'cpu_percent':95,'cores':4,'memory':{'total':2147483648,'used':1610612736,'available':536870912,'percent':75},'swap':{'total':0,'used':0,'percent':0},'system_disk':{'total':32000000000,'used':6400000000,'percent':20},'ssd':None,'temperature_c':83,'uptime_seconds':3600,'load':[1,1,1],'sampled_at':time.time()}))
            page.locator('[data-settings-tab=system]').click()
            expect(page.locator('.usage-ring[data-level=critical]')).to_have_count(1)
            expect(page.locator('.usage-ring[data-level=warning]')).to_have_count(1)
            page.evaluate('() => scrollTo(0,0)');no_overflow(page);screenshot(page,'system-mobile-dark')
            # Actual file toolbar and destructive dialog, desktop/mobile and light/dark.
            for width,dark in [(1360,False),(1360,True),(390,False),(390,True)]:
                page.set_viewport_size({'width':width,'height':940 if width>760 else 844})
                if page.locator('body').evaluate('(el)=>el.classList.contains("dark")')!=dark:
                    if width>760:page.locator('#themeBtn').click()
                    else:
                        page.locator('[data-page=settings]').click()
                        page.locator('[data-settings-tab=account]').click()
                        page.locator('[data-theme]').click()
                page.locator('[data-page=files]').click()
                page.locator('[data-file-view=list]').click()
                folder=page.locator('#fileList [data-file-folder="Travel photos"]')
                expect(folder).to_be_visible()
                muted='rgb(165, 169, 180)' if dark else 'rgb(108, 114, 108)'
                assert folder.locator('span').first.evaluate('(el)=>getComputedStyle(el).color')==muted
                page.locator('#fileSort').select_option('name-asc')
                for selector in ['#fileSort','#filePageSize']:
                    select=page.locator(selector)
                    style=select.evaluate('(el)=>{const s=getComputedStyle(el);return [s.appearance,s.textAlign,s.paddingLeft,s.paddingRight,s.textIndent];}')
                    assert style==['none','left','10px','30px','0px'],style
                page.locator('[data-file-view=grid]').click()
                expect(page.locator('.file-thumb.placeholder>span')).to_have_count(1)
                assert page.locator('.file-thumb.placeholder>span').evaluate('(el)=>getComputedStyle(el).color')==muted
                no_overflow(page);page.evaluate('() => scrollTo(0,0)')
                screenshot(page,'files-'+str(width)+('-dark' if dark else '-light'))
                page.locator('#fileList [data-file-folder="Travel photos"]').click()
                page.locator('#deleteFolder').click();expect(page.locator('#messageDialog')).to_be_visible()
                button=page.locator('#messageAccept')
                expect(button).to_have_class(__import__('re').compile('danger'))
                assert button.evaluate('(el)=>getComputedStyle(el).color')=='rgb(255, 255, 255)'
                assert button.evaluate('(el)=>getComputedStyle(el).backgroundColor')=='rgb(181, 61, 69)'
                button.hover();page.wait_for_timeout(200)
                assert button.evaluate('(el)=>getComputedStyle(el).color')=='rgb(255, 255, 255)'
                screenshot(page,'confirm-'+str(width)+('-dark' if dark else '-light'))
                page.locator('#messageCancel').click();page.locator('#filesUp').click()
            page.locator('[data-page=settings]').click()
            page.route('**/api/state',lambda r:r.abort())
            page.evaluate('() => refresh()');expect(page.locator('#connection')).to_have_class(__import__('re').compile('offline'))
            assert page.locator('#connection').evaluate('(el) => getComputedStyle(el).color')=='rgb(255, 148, 117)'
            page.unroute('**/api/state');page.evaluate('() => refresh()')
            expect(page.locator('#connection')).not_to_have_class(__import__('re').compile('offline'))
            assert page.locator('#connection').evaluate('(el) => getComputedStyle(el).color')=='rgb(105, 215, 165)'
            assert not errors,errors
            print('PASS: original blue/neutral light and dark palettes, centered settings, border-free slider tracks and compact engine rows verified; real UI verified default MP4, live link count, stable single favicon, range autosave 1–20 step 1, masked token, release-aware engine cards, usage chart, exact highlight share, backdrop dismissal and mobile/dark layouts without overflow.')
        finally:
            browser.close();site.shutdown();site.server_close();thread.join();desk.close()
