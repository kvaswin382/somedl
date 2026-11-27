"""Sandboxed Chromium: exact mixed carousel, authenticated JSON and DOM fallback.
All Instagram/CDN responses are controlled fixtures; no real account is used.
"""
import http.cookiejar
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, BrowserContext
import instagram_browser as ig

try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,chromium_sandbox=True,timeout=15000);browser.close()
except Exception as exc:
    print('UNAVAILABLE: sandboxed Chromium: '+str(exc)[:1000])
    raise SystemExit(1 if os.getenv('ZEDAIR_REQUIRE_BROWSER')=='1' else 77)

original_route=BrowserContext.route
cookie_checks=[]
def fixture_route(context,pattern,handler,**kwargs):
    def respond(route):
        url=route.request.url
        headers={'content-type':'text/html'}
        if '/p/JsonPost/' in url:
            body='''<script>fetch('/api/v1/media/info/').then(r=>r.json());fetch('/cookie-check?value='+encodeURIComponent(document.cookie));</script>'''
        elif '/api/v1/media/info/' in url:
            cookie_checks.append(route.request.all_headers().get('cookie',''))
            headers['content-type']='application/json'
            body=json.dumps({'data':[
                {'shortcode':'Advert','display_url':'https://cdn.example/ad.jpg'},
                {'shortcode':'JsonPost','owner':{'username':'alice'},'carousel_media_count':2,'carousel_media':[
                    {'image_versions2':{'candidates':[{'url':'https://cdn.example/low.jpg','width':320,'height':320},{'url':'https://cdn.example/high.jpg','width':1440,'height':1440}]}},
                    {'is_video':True,'video_versions':[{'url':'https://cdn.example/low.mp4','width':640,'height':360},{'url':'https://cdn.example/high.mp4','width':1920,'height':1080}]}]}]})
        elif '/p/DomPost/' in url:
            body='''<article><header><img src="https://cdn.example/avatar.jpg"></header><a href="/p/DomPost/">Post</a>
<img id="photo" style="width:500px;height:500px" src="https://cdn.example/one.jpg" srcset="https://cdn.example/one-high.jpg 1440w">
<button aria-label="Next" onclick="document.querySelector('#photo').srcset='https://cdn.example/two-high.jpg 1440w';this.remove()">Next</button></article>'''
        elif '/p/Login/' in url:
            return route.fulfill(status=302,headers={'location':'https://www.instagram.com/accounts/login/'},body='')
        elif '/cookie-check' in url:
            assert 'private_session' not in url,'HttpOnly cookie leaked into JavaScript';body='ok'
        elif 'cdn.example' in url:
            return route.fulfill(status=200,content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="1440"><rect width="1440" height="1440" fill="blue"/></svg>')
        else:body='<p>Login</p>'
        route.fulfill(status=200,headers=headers,body=body)
    return original_route(context,pattern,respond,**kwargs)

with tempfile.TemporaryDirectory() as root,patch.object(BrowserContext,'route',fixture_route),patch('instagram_browser.available_memory',return_value=1024**3):
    jar=http.cookiejar.MozillaCookieJar()
    jar.set_cookie(http.cookiejar.Cookie(0,'private_session','fixture',None,False,'.instagram.com',True,True,'/',True,True,None,True,None,None,{'HTTPOnly':''},False))
    spec={'state':root,'dest':root,'settings':{'browser_timeout':25,'browser_settle':2},'url':'https://www.instagram.com/p/JsonPost/'}
    items=ig.discover(spec,jar)
    assert [i['url'] for i in items]==['https://cdn.example/high.jpg','https://cdn.example/high.mp4'],items
    assert cookie_checks and 'private_session=fixture' in cookie_checks[0],cookie_checks
    items=ig.discover({**spec,'url':'https://www.instagram.com/p/DomPost/'},jar)
    assert [i['url'] for i in items]==['https://cdn.example/one-high.jpg','https://cdn.example/two-high.jpg'],items
    try:ig.discover({**spec,'url':'https://www.instagram.com/p/Login/'},jar)
    except ValueError as exc:assert 'verification' in str(exc),str(exc)
    else:raise AssertionError('Login wall was accepted')
print('PASS: sandboxed Instagram fixture selected exact high-resolution carousel, retained HttpOnly authentication, enumerated rendered carousel and rejected login wall.')
