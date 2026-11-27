"""Real FTP clients against rclone: authentication, listing, seeks and write denial."""
import ftplib,io,json,socket,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Desk

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);(root/'ssd/Zedair').mkdir(parents=True);remote=root/'remote';remote.mkdir();(remote/'Sub').mkdir()
    payload=b'0123456789'*100000;(remote/'Sub/movie.mp4').write_bytes(payload);(remote/'secret').write_bytes(b'outside')
    desk=Desk({'state':str(root/'state'),'mount':str(root/'ssd'),'require_mount':False})
    desk.remotes.config.write_text('[fixture]\ntype = alias\nremote = '+str(remote)+'\n')
    data={'remote':'fixture','path':'Sub','address':'127.0.0.1','protocol':'ftp','ftp_port':port(),'ftp_passive_min':port(),'username':'viewer','password':'fixture-stream-password','buffer_mib':8}
    data['ftp_passive_max']=data['ftp_passive_min']
    try:
        result=desk.remote_servers.start(data)
        assert set(result['urls'])=={'ftp'},result
        assert result['urls']['ftp'].startswith('ftp://127.0.0.1:')
        with ftplib.FTP() as ftp:
            ftp.connect('127.0.0.1',data['ftp_port'],timeout=10)
            try:ftp.login('viewer','wrong')
            except ftplib.error_perm:pass
            else:raise AssertionError('Wrong password accepted')
            ftp.login('viewer',data['password'])
            assert ftp.pwd()=='/'
            assert ftp.nlst()==['movie.mp4']
            assert dict(ftp.mlsd())['movie.mp4']['size']==str(len(payload))
            ftp.voidcmd('TYPE I');assert ftp.size('movie.mp4')==len(payload)
            output=io.BytesIO();ftp.retrbinary('RETR movie.mp4',output.write);assert output.getvalue()==payload
            output=io.BytesIO();ftp.retrbinary('RETR movie.mp4',output.write,rest=1234);assert output.getvalue()==payload[1234:]
            assert ftp.sendcmd('EPSV').startswith('229')
            for command in ('DELE movie.mp4','STOR new.bin','MKD Bad','RNFR movie.mp4','PORT 127,0,0,1,0,1','RETR ../secret'):
                try:ftp.sendcmd(command)
                except ftplib.all_errors:pass
                else:raise AssertionError('Unsafe command accepted: '+command)
        assert (remote/'Sub/movie.mp4').read_bytes()==payload and not list(desk.root.iterdir())
        assert 'password_hash' not in json.dumps(result) and data['password'] not in json.dumps(result)
        idle=ftplib.FTP();idle.connect('127.0.0.1',data['ftp_port'],timeout=10);idle.login('viewer',data['password'])
        desk.remote_servers.close()
        try:idle.voidcmd('NOOP')
        except (OSError,EOFError,ftplib.Error):pass
        else:raise AssertionError('Stopped server retained an authenticated session')
        idle.close()
        for mode,expected in [('http',{'http'}),('webdav',{'webdav'}),('all',{'http','webdav','ftp'})]:
            result=desk.remote_servers.start({**data,'protocol':mode,'http_port':port(),'webdav_port':port()})
            assert set(result['urls'])==expected,result
            desk.remote_servers.close()
        print('PASS: real FTP login, passive browsing, full/seek byte equality, write/active-mode denial, remote-root confinement; HTTP/WebDAV/FTP/all selections start and stop without SSD caching.')
    finally:desk.close()
