"""Real Transmission: duplicate confirmation and a fresh directory preserve old data.
Only loopback RPC/tracker addresses are used; no public torrent swarm is contacted.
"""
import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Desk
from torrent_add import add,fresh_copy


def encode(value):
    if isinstance(value,int):return b'i'+str(value).encode()+b'e'
    if isinstance(value,bytes):return str(len(value)).encode()+b':'+value
    return b'd'+b''.join(encode(k)+encode(v) for k,v in sorted(value.items()))+b'e'


def available_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def until(check):
    end=time.monotonic()+15
    while time.monotonic()<end:
        try:
            value=check()
            if value:return value
        except Exception:pass
        time.sleep(.1)
    raise AssertionError('Transmission did not reach the expected fixture state')


if not shutil.which('transmission-daemon'):
    print('UNAVAILABLE: transmission-daemon integration fixture')
    raise SystemExit(1 if os.getenv('ZEDAIR_REQUIRE_BROWSER')=='1' else 77)
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);config=root/'transmission';config.mkdir();mount=root/'ssd';old=mount/'Zedair/Old';old.mkdir(parents=True)
    content=b'existing completed torrent data\n'*100;(old/'fixture.bin').write_bytes(content)
    port=available_port()
    (config/'settings.json').write_text(json.dumps({'rpc-enabled':True,'rpc-port':port,'rpc-bind-address':'127.0.0.1',
        'rpc-authentication-required':False,'rpc-whitelist':'127.0.0.1','rpc-whitelist-enabled':True,
        'rpc-host-whitelist-enabled':False,'dht-enabled':False,'pex-enabled':False,'lpd-enabled':False,
        'port-forwarding-enabled':False,'peer-port':available_port(),'bind-address-ipv4':'127.0.0.1','bind-address-ipv6':'::1',
        'download-dir':str(old),'start-added-torrents':False,'watch-dir-enabled':False,'preallocation':0}))
    with (root/'daemon.log').open('w+') as log:
        process=subprocess.Popen(['transmission-daemon','-f','-g',str(config)],stdout=log,stderr=log)
        desk=Desk({'state':str(root/'state'),'mount':str(mount),'require_mount':False,'rpc_url':f'http://127.0.0.1:{port}/transmission/rpc'})
        desk.set_setting('reserve_gib',0)
        try:
            until(lambda:desk.rpc.call('session-get'))
            info={b'length':len(content),b'name':b'fixture.bin',b'piece length':16384,b'pieces':hashlib.sha1(content).digest(),b'private':1}
            meta=base64.b64encode(encode({b'announce':f'http://127.0.0.1:{port}/announce'.encode(),b'info':info})).decode()
            first=add(desk,{'folder':'Old','metainfo':meta,'paused':True});key=first['hash']
            def current():return desk.rpc.call('torrent-get',{'ids':[key],'fields':['status','percentDone','downloadDir']})['torrents'][0]
            desk.rpc.call('torrent-verify',{'ids':[key]})
            until(lambda:current()['status']==0 and current()['percentDone']==1)
            duplicate=add(desk,{'folder':'New copies','metainfo':meta})
            assert duplicate['status']=='duplicate' and current()['status']==0
            assert Path(current()['downloadDir'])==old and (old/'fixture.bin').read_bytes()==content
            assert not (desk.root/duplicate['folder']).exists()
            result=fresh_copy(desk,{'token':duplicate['token'],'confirm':True})
            until(lambda:current()['status'] in (3,4) and current()['percentDone']==0)
            assert Path(current()['downloadDir'])==desk.root/result['folder']
            assert (old/'fixture.bin').read_bytes()==content
            assert fresh_copy(desk,{'token':duplicate['token'],'confirm':True})==result
            assert len(desk.rpc.call('torrent-get',{'fields':['id']})['torrents'])==1
            print('PASS: real Transmission left an unconfirmed duplicate paused, verified a confirmed fresh empty location, started the same queue entry at zero completion, kept the old exact bytes, and handled confirmation replay without a second copy.')
        except BaseException:
            log.flush();log.seek(0);print(log.read()[-5000:]);raise
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            desk.close()
