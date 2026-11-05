"""Offline regression using the installed gallery-dl cookie reader/writer, no real account."""
from pathlib import Path
import http.cookiejar
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gallery_dl import util
import platforms

with tempfile.TemporaryDirectory() as folder:
    state=Path(folder)
    platforms.save_cookies(state,'instagram',{'fields':{'sessionid':'fixture-session'}})
    source=state/'cookies-instagram.txt'
    # Reproduce the old bug: gallery-dl's real writer emits 0 for session expiry.
    with source.open() as stream: cookies=util.cookiestxt_load(stream)
    with source.open('w') as stream: util.cookiestxt_store(stream,cookies)
    assert '\t0\tsessionid\t' in source.read_text()
    old_reader=http.cookiejar.MozillaCookieJar(str(source))
    old_reader.load(ignore_discard=True,ignore_expires=True)
    assert next(c for c in old_reader if c.name=='sessionid').expires == 0
    assert platforms.cookie_status(state,'instagram')['usable']
    for i in range(3):
        lease=platforms.InstagramSession(source)
        with lease.path.open() as stream: cookies=util.cookiestxt_load(stream)
        session=next(c for c in cookies if c.name=='sessionid')
        assert session.expires is None
        assert session.value==('fixture-session' if i==0 else f'rotated-fixture-{i-1}')
        session.value=f'rotated-fixture-{i}'  # Simulated server rotation, never a real credential.
        with lease.path.open('w') as stream: util.cookiestxt_store(stream,cookies)
        assert lease.commit()
        assert platforms.cookie_status(state,'instagram')['usable']
        lease.close()
    assert not list(state.glob('cookies-work-*'))
print('PASS: real gallery-dl cookie serialization, zero-expiry recovery and three session rotations preserved across jobs; no Instagram requests made.')
