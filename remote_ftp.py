"""Read-only passive FTP gateway to the private rclone WebDAV listener.

No local files, write commands, active-mode connections or third-party packages.
Shares the HTTP/WebDAV connection and RAM budgets.
"""
import base64
import email.utils
import hmac
import http.client
import posixpath
import queue
import socket
import socketserver
import threading
import xml.etree.ElementTree as ET
from urllib.parse import quote,unquote,urlsplit


class FTPHandler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(60)
        self.user='';self.authenticated=False;self.cwd='/';self.offset=0;self.passive=None
        with self.server.owner.lock:self.server.owner.ftp_clients.add(self.connection)
    def finish(self):
        with self.server.owner.lock:self.server.owner.ftp_clients.discard(self.connection)
        super().finish()
    def reply(self,code,message):
        self.wfile.write((str(code)+' '+message+'\r\n').encode('utf-8'))
    def path(self,arg):
        if '\x00' in arg or '\r' in arg or '\n' in arg:raise ValueError('Invalid path')
        return '/'+posixpath.normpath('/'+posixpath.join(self.cwd,arg).lstrip('/')).lstrip('/')
    def upstream(self,method,path,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.upstream,timeout=30)
        auth=base64.b64encode(('zedair:'+self.server.owner.internal_password).encode()).decode()
        c.request(method,quote(path,safe='/'),headers={'Authorization':'Basic '+auth,**(headers or {})})
        c._zedwire=c.sock
        try:return c,c.getresponse()
        except BaseException:c.close();raise
    def entries(self,path,depth='1'):
        c,r=self.upstream('PROPFIND',path,{'Depth':depth})
        try:
            if r.status!=207:raise ValueError('File or folder unavailable')
            raw=r.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:raise ValueError('Folder listing exceeds 4 MiB; open a smaller subfolder')
            result=[]
            for item in ET.fromstring(raw).findall('{DAV:}response'):
                name=unquote(urlsplit(item.findtext('{DAV:}href','')).path).rstrip('/') or '/'
                prop=item.find('.//{DAV:}prop')
                if prop is None:continue
                directory=prop.find('{DAV:}resourcetype/{DAV:}collection') is not None
                date=item.findtext('.//{DAV:}getlastmodified','')
                try:stamp=email.utils.parsedate_to_datetime(date).strftime('%Y%m%d%H%M%S')
                except (ValueError,TypeError,AttributeError):stamp='19700101000000'
                size=int(prop.findtext('{DAV:}getcontentlength','0') or 0)
                result.append({'path':name,'dir':directory,'size':size,'stamp':stamp})
            return result
        finally:r.close();c.close()
    def stat(self,path):
        values=self.entries(path,'0')
        return next((v for v in values if v['path']==path.rstrip('/') or (path=='/' and v['path']=='/')),None)
    def close_passive(self):
        if self.passive:self.passive.close();self.passive=None
    def passive_mode(self,extended):
        self.close_passive();options=self.server.owner.options
        for port in range(options['ftp_passive_min'],options['ftp_passive_max']+1):
            candidate=socket.socket();candidate.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try:candidate.bind((self.connection.getsockname()[0],port));candidate.listen(1);candidate.settimeout(15)
            except OSError:candidate.close();continue
            self.passive=candidate;break
        if not self.passive:return self.reply(425,'No passive data port available')
        port=self.passive.getsockname()[1]
        if extended:self.reply(229,'Entering Extended Passive Mode (|||'+str(port)+'|)')
        else:self.reply(227,'Entering Passive Mode ('+self.connection.getsockname()[0].replace('.',',')+','+str(port//256)+','+str(port%256)+')')
    def send_data(self,source):
        owner=self.server.owner
        if not self.passive:return self.reply(425,'Use PASV or EPSV first')
        if not owner.streams.acquire(blocking=False):self.close_passive();return self.reply(425,'Two streams are already open')
        data=None;connection=None;response=None;producer=None;done=threading.Event()
        with owner.lock:owner.readers+=1
        try:
            if isinstance(source,str):
                connection,response=self.upstream('GET',source,{'Range':'bytes='+str(self.offset)+'-'} if self.offset else {})
                if response.status not in (200,206) or (self.offset and response.status!=206):raise ValueError('Remote file or seek position unavailable')
            self.reply(150,'Opening binary data connection')
            data,peer=self.passive.accept();self.close_passive();data.settimeout(30)
            with owner.lock:owner.ftp_clients.add(data)
            if peer[0]!=self.client_address[0]:raise ValueError('Data connection must come from the logged-in client')
            if isinstance(source,bytes):data.sendall(source)
            else:
                blocks=queue.Queue(maxsize=max(1,owner.options['buffer_mib']*1024**2//2//65536))
                def put(value):
                    while not done.is_set() and not owner.stopping.is_set():
                        try:blocks.put(value,timeout=.2);return
                        except queue.Full:pass
                def read():
                    try:
                        while not done.is_set() and not owner.stopping.is_set():
                            block=response.read(65536)
                            if not block:break
                            put(block)
                    except Exception as exc:put(exc)
                    finally:put(None)
                producer=threading.Thread(target=read,daemon=True);producer.start()
                while not owner.stopping.is_set():
                    try:block=blocks.get(timeout=1)
                    except queue.Empty:continue
                    if block is None:break
                    if isinstance(block,Exception):raise block
                    data.sendall(block)
                if owner.stopping.is_set():raise ValueError('Streaming stopped')
            with owner.lock:owner.ftp_clients.discard(data)
            data.close();data=None;self.reply(226,'Transfer complete')
        except (OSError,ValueError,http.client.HTTPException):self.reply(426,'Transfer failed or interrupted')
        finally:
            done.set();self.close_passive();self.offset=0
            if data:
                with owner.lock:owner.ftp_clients.discard(data)
                data.close()
            if connection:
                if connection._zedwire:
                    try:connection._zedwire.shutdown(socket.SHUT_RDWR)
                    except OSError:pass
                connection.close()
            if producer:producer.join(timeout=2)
            if response:response.close()
            with owner.lock:owner.readers-=1
            owner.streams.release()
    def handle(self):
        from server import password_ok
        owner=self.server.owner;failures=0
        try:
            self.reply(220,'Zedair read-only FTP; passive mode; LAN only')
            while not owner.stopping.is_set():
                line=self.rfile.readline(4097)
                if not line or len(line)>4096:break
                command,_,arg=line.decode('utf-8').rstrip('\r\n').partition(' ');command=command.upper()
                if command=='QUIT':self.reply(221,'Goodbye');break
                if command=='USER':self.close_passive();self.offset=0;self.user=arg;self.authenticated=False;self.reply(331,'Password required');continue
                if command=='PASS':
                    self.authenticated=bool(self.user.isascii() and hmac.compare_digest(self.user,owner.options['username']) and len(arg)<=512 and password_ok(arg,owner.options['password_hash']))
                    self.reply(230 if self.authenticated else 530,'Logged in read-only' if self.authenticated else 'Login incorrect')
                    if not self.authenticated:failures+=1
                    if failures>=3:break
                    continue
                if command=='FEAT':self.wfile.write(b'211-Features\r\n UTF8\r\n EPSV\r\n PASV\r\n REST STREAM\r\n SIZE\r\n MDTM\r\n MLST type*;size*;modify*;\r\n211 End\r\n');continue
                if not self.authenticated:self.reply(530,'Login first');continue
                try:
                    if command in ('PWD','XPWD'):self.reply(257,'"'+self.cwd.replace('"','""')+'"')
                    elif command=='SYST':self.reply(215,'UNIX Type: L8')
                    elif command in ('NOOP','TYPE','OPTS'):self.reply(200,'OK')
                    elif command in ('CWD','CDUP'):
                        path=self.path('..' if command=='CDUP' else arg);entry=self.stat(path)
                        if not entry or not entry['dir']:raise ValueError('Folder unavailable')
                        self.cwd=path;self.reply(250,'Directory changed')
                    elif command in ('PASV','EPSV'):self.passive_mode(command=='EPSV')
                    elif command=='REST':
                        if not arg.isdigit():raise ValueError('Invalid seek offset')
                        self.offset=int(arg);self.reply(350,'Restart position accepted')
                    elif command=='MLST':
                        path=self.path(arg);entry=self.stat(path)
                        if not entry:raise ValueError('File unavailable')
                        facts=('type=dir;' if entry['dir'] else 'type=file;')+'size='+str(entry['size'])+';modify='+entry['stamp']+'; '+path
                        self.wfile.write(('250-Listing\r\n '+facts+'\r\n250 End\r\n').encode('utf-8'))
                    elif command in ('SIZE','MDTM'):
                        entry=self.stat(self.path(arg))
                        if not entry or entry['dir']:raise ValueError('File unavailable')
                        self.reply(213,str(entry['size']) if command=='SIZE' else entry['stamp'])
                    elif command=='RETR':self.send_data(self.path(arg))
                    elif command in ('LIST','NLST','MLSD'):
                        path=self.path('' if arg in ('-a','-l','-al','-la') else arg);lines=[]
                        for item in self.entries(path):
                            if item['path']==path.rstrip('/') or item['path']=='/':continue
                            name=posixpath.basename(item['path'])
                            if '\r' in name or '\n' in name:continue
                            if command=='NLST':lines.append(name)
                            elif command=='MLSD':lines.append(('type=dir;' if item['dir'] else 'type=file;')+'size='+str(item['size'])+';modify='+item['stamp']+'; '+name)
                            else:lines.append(('dr-xr-xr-x' if item['dir'] else '-r--r--r--')+' 1 owner group '+str(item['size'])+' Jan 01 1970 '+name)
                        self.send_data(('\r\n'.join(lines)+'\r\n').encode('utf-8'))
                    else:self.reply(502,'Read-only passive FTP; command not supported')
                except (ValueError,OSError,ET.ParseError,http.client.HTTPException):self.reply(550,'Requested file or operation unavailable')
        except (OSError,UnicodeError):pass
        finally:self.close_passive()
