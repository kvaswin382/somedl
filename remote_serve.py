"""Read-only LAN streaming through rclone, with a bounded shared RAM read-ahead.

Two active responses share the selected budget. rclone has no file buffers or
disk cache; the proxy queues at most budget/2 per response (plus small I/O blocks).
"""
import base64
import hmac
import http.client
import http.server
import ipaddress
import json
import os
import queue
import secrets
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit


def addresses():
    values={'127.0.0.1'}
    try:
        p=subprocess.run(['ip','-j','-4','address'],capture_output=True,text=True,check=True,timeout=3)
        for interface in json.loads(p.stdout):
            for item in interface.get('addr_info',[]):
                value=item.get('local','')
                if lan(value):values.add(value)
    except (OSError,ValueError,subprocess.SubprocessError):pass
    return sorted(values,key=lambda v:(v=='127.0.0.1',v))

def lan(value):
    try:
        ip=ipaddress.ip_address(value)
        return ip.version==4 and (ip.is_loopback or any(ip in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')))
    except ValueError:return False


class Server(http.server.ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def process_request(self,request,address):
        # Cap threads before parsing credentials or starting remote requests.
        if not lan(address[0]) or not self.owner.requests.acquire(blocking=False):
            request.close();return
        try:super().process_request(request,address)
        except BaseException:self.owner.requests.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.owner.requests.release()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass  # Never log Authorization or file paths.
    def setup(self):
        super().setup();self.connection.settimeout(30)
    def reply(self,code,message='',authenticate=False):
        raw=message.encode();self.send_response(code)
        self.send_header('Content-Length',str(len(raw)));self.send_header('Connection','close')
        if authenticate:self.send_header('WWW-Authenticate','Basic realm="Zedair streaming"')
        self.end_headers();self.close_connection=True
        if self.command!='HEAD':self.wfile.write(raw)
    def handle_stream(self):
        from server import password_ok
        owner=self.server.owner;settings=owner.options
        try:
            raw=base64.b64decode(self.headers.get('Authorization','').removeprefix('Basic '),validate=True).decode()
            user,secret=raw.split(':',1)
            if len(raw)>8192 or len(secret)>512 or not user.isascii() or not hmac.compare_digest(user,settings['username']) or not password_ok(secret,settings['password_hash']):raise ValueError()
        except (ValueError,UnicodeError):return self.reply(401,'Enter the streaming username and password.',True)
        if self.command not in ('GET','HEAD','OPTIONS','PROPFIND'):return self.reply(405,'Read-only streaming')
        if self.command=='PROPFIND' and self.server.protocol!='webdav':return self.reply(405)
        if not self.path.startswith('/') or self.path.startswith('//') or urlsplit(self.path).scheme:return self.reply(400)
        if self.headers.get('Transfer-Encoding'):return self.reply(400)
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:return self.reply(400)
        if not 0<=length<=65536:return self.reply(413)
        if self.headers.get('Depth','1') not in ('0','1'):return self.reply(403,'Use Depth 0 or 1')
        body=self.rfile.read(length)
        if not owner.streams.acquire(blocking=False):return self.reply(503,'Two streams are already open. Try again shortly.')
        connection=http.client.HTTPConnection('127.0.0.1',self.server.upstream,timeout=30);wire=None
        done=threading.Event();producer=None
        with owner.lock:owner.readers+=1
        try:
            headers={k:self.headers[k] for k in ('Range','If-Range','If-None-Match','If-Modified-Since','Content-Type','Depth') if k in self.headers}
            headers['Authorization']='Basic '+base64.b64encode(('zedair:'+owner.internal_password).encode()).decode()
            connection.request(self.command,self.path,body,headers);wire=connection.sock;response=connection.getresponse()
            self.send_response(response.status)
            for key in ('Content-Type','Content-Length','Content-Range','Accept-Ranges','ETag','Last-Modified','DAV','Allow'):
                if response.getheader(key) is not None:self.send_header(key,response.getheader(key))
            location=response.getheader('Location','')
            if location.startswith('/') and not location.startswith('//'):self.send_header('Location',location)
            self.send_header('Connection','close');self.end_headers();self.close_connection=True
            if self.command=='HEAD':return
            # Queue slots are fixed 64 KiB, divided over the two shared readers.
            blocks=queue.Queue(maxsize=max(1,settings['buffer_mib']*1024*1024//2//65536))
            def put(value):
                while not done.is_set() and not owner.stopping.is_set():
                    try:blocks.put(value,timeout=.2);return True
                    except queue.Full:pass
                return False
            def read():
                try:
                    while not done.is_set() and not owner.stopping.is_set():
                        block=response.read(65536)
                        if not block:break
                        if not put(block):return
                except Exception as exc:put(exc)
                finally:put(None)
            producer=threading.Thread(target=read,daemon=True);producer.start()
            while not owner.stopping.is_set():
                try:block=blocks.get(timeout=1)
                except queue.Empty:continue
                if block is None:break
                if isinstance(block,Exception):raise block
                self.wfile.write(block)
        except (OSError,http.client.HTTPException):self.close_connection=True
        finally:
            done.set()
            if wire:
                try:wire.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            connection.close()
            if producer:producer.join(timeout=2)
            with owner.lock:owner.readers-=1
            owner.streams.release()
    do_GET=do_HEAD=do_OPTIONS=do_PROPFIND=do_PUT=do_DELETE=do_MKCOL=do_MOVE=do_COPY=do_POST=do_PATCH=handle_stream


class RemoteServers:
    def __init__(self,desk):
        self.desk=desk;self.lock=threading.RLock();self.control=threading.Lock()
        self.processes=[];self.servers=[];self.threads=[];self.options={};self.readers=0
        self.streams=threading.BoundedSemaphore(2);self.requests=threading.BoundedSemaphore(8)
        self.stopping=threading.Event();self.error=''
    def active(self):return bool(self.servers)
    def public(self):
        saved=self.desk.get_setting('remote_serve',{})
        return {'running':self.active() and all(p.poll() is None for p in self.processes),
            'readers':self.readers,'error':self.error,'addresses':addresses(),
            'settings':{k:v for k,v in saved.items() if k!='password_hash'},'password_set':bool(saved.get('password_hash')),
            'urls':{s.protocol:'http://'+self.options['address']+':'+str(s.server_port)+'/' for s in self.servers}}
    def start(self,data):
        from server import password_hash
        with self.control:
            if self.readers:raise ValueError('Previous streams are closing; try again in a moment')
            if self.active():raise ValueError('Stop streaming before changing its settings')
            remote=data.get('remote');path=data.get('path','');target=self.desk.remotes.read_target(remote,path)
            address=data.get('address')
            if address not in addresses():raise ValueError('Select this laptop’s LAN address')
            ports=[data.get('http_port',8090),data.get('webdav_port',8091)]
            if any(type(n) is not int or not 1024<=n<=65535 for n in ports) or ports[0]==ports[1]:raise ValueError('Choose two different ports from 1024 to 65535')
            budget=data.get('buffer_mib',64)
            if type(budget) is not int or not 8<=budget<=512:raise ValueError('RAM buffer must be 8–512 MiB')
            user=data.get('username','')
            if not isinstance(user,str) or not user or len(user)>64 or any(c in user for c in ':\r\n') or not user.isascii():raise ValueError('Use a short ASCII streaming username without a colon')
            secret=data.get('password','');saved=self.desk.get_setting('remote_serve',{})
            if secret and (not isinstance(secret,str) or not 12<=len(secret)<=512):raise ValueError('Use a streaming password of 12–512 characters')
            hashed=password_hash(secret) if secret else saved.get('password_hash')
            if not hashed:raise ValueError('Set a streaming password')
            self.options={'remote':remote,'path':path,'address':address,'http_port':ports[0],'webdav_port':ports[1],'buffer_mib':budget,'username':user,'password_hash':hashed}
            self.stopping.clear();self.error='';self.internal_password=secrets.token_urlsafe(32)
            try:
                for protocol,port in zip(('http','webdav'),ports):
                    front=Server((address,port),Handler);front.owner=self;front.protocol=protocol;self.servers.append(front)
                    with socket.socket() as probe:probe.bind(('127.0.0.1',0));upstream=probe.getsockname()[1]
                    front.upstream=upstream
                    args=self.desk.remotes.args('serve',protocol,target,'--addr','127.0.0.1:'+str(upstream),'--read-only','--vfs-cache-mode','off','--buffer-size','0','--dir-cache-time','30s','--poll-interval','0','--no-modtime')
                    env={**os.environ,'RCLONE_USER':'zedair','RCLONE_PASS':self.internal_password}
                    p=subprocess.Popen(args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True);self.processes.append(p)
                    ready=False
                    for _ in range(50):
                        if p.poll() is not None:break
                        try:
                            with socket.create_connection(('127.0.0.1',upstream),timeout=.1):ready=True;break
                        except OSError:time.sleep(.1)
                    if not ready:raise ValueError('Rclone streaming could not start; check the remote and installed rclone version')
                    def drain(process=p):
                        from notifications import sanitized
                        for line in process.stderr:
                            if b'ERROR' in line or b'Failed' in line:self.error=sanitized(line.decode('utf-8','replace'))[-1200:]
                    threading.Thread(target=drain,daemon=True).start()
                    thread=threading.Thread(target=front.serve_forever,daemon=True);thread.start();self.threads.append(thread)
                self.desk.set_setting('remote_serve',self.options)
                from activities import record
                record(self.desk,'remote streaming started',remote+':'+path)
            except Exception:self._stop();raise
        return self.public()
    def _stop(self):
        self.stopping.set()
        for index,s in enumerate(self.servers):
            if index<len(self.threads):s.shutdown()
            s.server_close()
        for p in self.processes:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            if p.stderr:p.stderr.close()
        self.servers=[];self.processes=[];self.threads=[]
    def close(self):
        with self.control:self._stop()
