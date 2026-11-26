"""Real rclone HTTP/WebDAV: authenticated ranges, browsing, read-only and no cache."""
import base64,http.client,json,socket,sys,tempfile,time,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Desk

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);(root/'ssd/Zedair').mkdir(parents=True);remote=root/'remote';remote.mkdir();(remote/'Sub').mkdir()
    payload=b'0123456789'*100000;(remote/'Sub/movie.mp4').write_bytes(payload)
    desk=Desk({'state':str(root/'state'),'mount':str(root/'ssd'),'require_mount':False})
    password='fixture-stream-password';auth='Basic '+base64.b64encode(('viewer:'+password).encode()).decode()
    desk.remotes.config.write_text('[disk]\ntype = local\n[fixture]\ntype = alias\nremote = '+str(remote)+'\n')
    try:
        result=desk.remote_servers.start({'remote':'fixture','path':'Sub','address':'127.0.0.1','http_port':port(),'webdav_port':port(),'buffer_mib':8,'username':'viewer','password':password})
        assert result['running'],result
        assert 'password_hash' not in result['settings'] and password not in json.dumps(result)
        def request(protocol,method,path,headers=None,body=None):
            c=http.client.HTTPConnection('127.0.0.1',result['settings'][protocol+'_port'],timeout=10)
            try:
                c.request(method,path,body,headers or {});r=c.getresponse();return r.status,r.getheaders(),r.read()
            finally:c.close()
        for protocol in ('http','webdav'):
            code,headers,body=request(protocol,'GET','/movie.mp4');assert code==401,(protocol,code)
            code,headers,body=request(protocol,'GET','/movie.mp4',{'Authorization':auth,'Range':'bytes=100-1099'})
            assert code==206 and body==payload[100:1100],(protocol,code,len(body),body[:80])
            code,headers,body=request(protocol,'GET','/movie.mp4',{'Authorization':auth});assert code==200 and body==payload
            code,_,_=request(protocol,'DELETE','/movie.mp4',{'Authorization':auth});assert code==405
            code,_,_=request(protocol,'PUT','/new.bin',{'Authorization':auth},b'bad');assert code==405
        code,_,body=request('webdav','PROPFIND','/',{'Authorization':auth,'Depth':'1'});assert code==207 and b'movie.mp4' in body
        assert (remote/'Sub/movie.mp4').read_bytes()==payload;assert not (remote/'Sub/new.bin').exists()
        assert not list(desk.root.iterdir()),list(desk.root.iterdir())
        desk.remote_servers.close()
        assert not desk.remote_servers.public()['running']
        job={'cancel':threading.Event(),'bytes':0,'detail':'','process':None}
        copy={'remote':'fixture','path':'Sub/movie.mp4','target_remote':'fixture','target_path':'Copies/movie.mp4'}
        desk.remotes.copy(job,copy)
        assert (remote/'Copies/movie.mp4').read_bytes()==payload
        desk.remotes.copy(job,copy)  # Identical destination is safely skipped.
        (remote/'Copies/movie.mp4').write_bytes(b'keep existing bytes')
        desk.remotes.copy(job,copy)
        assert (remote/'Copies/movie.mp4').read_bytes()==b'keep existing bytes'
        assert not list(desk.root.iterdir())
        print('PASS: real remote copy preserves exact bytes and skips all existing destinations without SSD staging.')
        print('PASS: actual rclone HTTP and WebDAV require credentials; byte-range seeks and full reads match; writes refused; no SSD cache; servers stop cleanly.')
    finally:desk.close()
