"""Private rclone configuration, confined remote browsing and streamed uploads."""
import configparser
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
from pathlib import Path
from notifications import sanitized

def remote_name(value):
    return isinstance(value,str) and bool(re.fullmatch(r'[\w.][\w .-]{0,127}',value)) and value not in ('.','..')

def merge_config(destination,content):
    """Merge new named remotes without replacing existing credentials."""
    import io
    if not isinstance(content,str) or len(content.encode())>1024*1024:raise ValueError('Config must be smaller than 1 MiB')
    source=configparser.RawConfigParser();existing=configparser.RawConfigParser()
    try:
        source.read_string(content)
        if destination.exists():existing.read_string(destination.read_text())
    except configparser.Error:raise ValueError('Invalid rclone config. For encrypted config, supply its password.') from None
    if source.defaults() or not source.sections():raise ValueError('No named remotes found in this config')
    for name in source.sections():
        if not remote_name(name) or not source.get(name,'type',fallback=''):raise ValueError('Invalid remote name or missing backend type')
    added=[];skipped=[]
    for name in source.sections():
        if existing.has_section(name):skipped.append(name);continue
        existing.add_section(name)
        for key,value in source.items(name):existing.set(name,key,value)
        added.append(name)
    if added:
        if destination.is_symlink():raise ValueError('Config must not be a symlink')
        temp=destination.with_name('.rclone-import-'+secrets.token_hex(6))
        try:
            with temp.open('x') as stream:
                temp.chmod(0o600);existing.write(stream);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,destination)
        finally:temp.unlink(missing_ok=True)
    return {'added':added,'skipped':skipped}

def import_login_config(state):
    """Installer imports only the invoking sudo user's own regular config."""
    import pwd,stat
    user=os.environ.get('SUDO_USER','')
    if not user or user=='root':return
    account=pwd.getpwnam(user);source=Path(account.pw_dir)/'.config/rclone/rclone.conf'
    try:
        # No arbitrary path from the browser and no root-owned credential file.
        fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    except FileNotFoundError:return
    try:
        st=os.fstat(fd)
        if st.st_uid!=account.pw_uid or not stat.S_ISREG(st.st_mode) or st.st_size>1024*1024:raise ValueError('Unsafe login-user rclone config')
        with os.fdopen(fd,'r',closefd=False) as stream:content=stream.read(1024*1024+1)
        result=merge_config(state/'rclone.conf',content)
        owner=pwd.getpwnam('zedair');os.chown(state/'rclone.conf',owner.pw_uid,owner.pw_gid)
        print('Imported rclone remotes: '+(', '.join(result['added']) or 'none new')+'. Existing app remotes preserved.')
    except (ValueError,UnicodeError):print('Rclone config could not be imported automatically. Use Settings → Rclone → Import config.')
    finally:os.close(fd)


def relative(value):
    if not isinstance(value,str) or len(value)>1024 or any(ord(c)<32 for c in value) or '\\' in value or ':' in value or value.startswith('/'):
        raise ValueError('Use a path inside the selected remote')
    bits=value.rstrip('/').split('/') if value else []
    if any(b in ('','.','..') for b in bits):raise ValueError('Use a path inside the selected remote')
    return '/'.join(bits)


class Remotes:
    def __init__(self,desk):
        self.desk=desk;self.config=desk.state/'rclone.conf';self.lock=threading.RLock();self.questions={};self.responses={}
        self.session_path=desk.state/'rclone-setup.json'
        try:self.responses=json.loads(self.session_path.read_text());self.questions={k:v.get('State','') for k,v in self.responses.items()}
        except (OSError,ValueError,TypeError):pass
        if self.config.is_symlink():raise ValueError('rclone config must not be a symlink')
        self.config.touch(mode=0o600,exist_ok=True);self.config.chmod(0o600)

    def args(self,*args):
        binary=shutil.which('rclone')
        if not binary:raise ValueError('rclone is not installed. Run the project upgrade script.')
        return [binary,'--config',str(self.config),'--contimeout','15s','--timeout','60s',*args]

    def run(self,*args,json_output=True,timeout=90):
        with self.lock:
            try:p=subprocess.run(self.args(*args),capture_output=True,text=True,timeout=timeout)
            except subprocess.TimeoutExpired:raise ValueError('rclone operation timed out; check the remote connection and try again') from None
            self.config.chmod(0o600)
        if p.returncode:raise ValueError('rclone: '+sanitized(p.stderr)[-1800:])
        if len(p.stdout)>16*1024*1024:raise ValueError('Remote listing is too large; choose a smaller folder')
        return json.loads(p.stdout or '{}') if json_output else p.stdout

    def names(self):
        return [s[:-1] for s in self.run('listremotes',json_output=False).splitlines() if s.endswith(':') and remote_name(s[:-1])]

    def import_config(self,data):
        with self.lock:
            if self.desk.workers or self.desk.tasks.busy() or getattr(self.desk,'remote_servers',None) and self.desk.remote_servers.active():raise ValueError('Finish cloud work and stop streaming before importing config')
            content=data.get('content','')
            if not isinstance(content,str) or len(content.encode())>1024*1024:raise ValueError('Config must be smaller than 1 MiB')
            if content.startswith('RCLONE_ENCRYPT_V0:'):
                password=data.get('password','')
                if not isinstance(password,str) or not password or len(password)>4096:raise ValueError('Enter the encrypted config password')
                temp=self.desk.state/('.rclone-encrypted-'+secrets.token_hex(6))
                try:
                    with temp.open('x') as stream:temp.chmod(0o600);stream.write(content)
                    env={**os.environ,'RCLONE_CONFIG_PASS':password}
                    p=subprocess.run([shutil.which('rclone'),'--config',str(temp),'config','dump'],env=env,capture_output=True,text=True,timeout=15)
                    if p.returncode:raise ValueError('Could not decrypt config; check its password')
                    raw=json.loads(p.stdout);parser=configparser.RawConfigParser()
                    for name,values in raw.items():parser[name]={k:str(v) for k,v in values.items()}
                    import io
                    stream=io.StringIO();parser.write(stream);content=stream.getvalue()
                finally:temp.unlink(missing_ok=True)
            result=merge_config(self.config,content)
            from activities import record
            record(self.desk,'rclone config imported',', '.join(result['added']))
            return result

    def files(self,name,path=''):
        entries=self.run('lsjson',self.target(name,path),'--max-depth','1','--no-mimetype','--no-modtime')
        safe=[]
        for e in entries:
            n=e.get('Name','')
            try:valid=relative(n)==n and '/' not in n and bool(n)
            except ValueError:valid=False
            if valid:safe.append({'name':n,'directory':bool(e.get('IsDir')),'size':max(0,int(e.get('Size',0)))})
        return {'remote':name,'path':relative(path),'entries':sorted(safe,key=lambda e:(not e['directory'],e['name'].casefold()))}

    def copy(self,job,data):
        source=self.target(data.get('remote'),data.get('path',''))
        destination=self.target(data.get('target_remote'),data.get('target_path',''))
        if source==destination:raise ValueError('Choose a different destination')
        if data.get('directory') is True:
            if not data.get('path'):raise ValueError('Select a folder, not the whole remote')
            if data.get('remote')==data.get('target_remote') and data.get('target_path','').startswith(data['path']+'/'):raise ValueError('Cannot copy a folder into itself')
        # Older rclone versions do not consistently enforce --immutable for copyto.
        # Skip every existing destination so a copy never replaces cloud files.
        args=self.args('copy' if data.get('directory') is True else 'copyto',source,destination,'--ignore-existing','--immutable','--transfers','1','--checkers','1','--buffer-size','1M','--stats','1s','--use-json-log','--retries','1')
        p=self.desk.tasks.process(job,args,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        tail=[]
        def monitor():
            for line in p.stderr:
                try:
                    entry=json.loads(line);stats=entry.get('stats',{})
                    if 'bytes' in stats:job['bytes']=stats['bytes'];job['detail']='Copying · '+str(stats.get('transfers',0))+' files completed'
                    elif entry.get('level')=='error':tail.append(sanitized(entry.get('msg','')));del tail[:-3]
                except (ValueError,TypeError):pass
        thread=threading.Thread(target=monitor,daemon=True);thread.start()
        try:self.desk.tasks.wait(job,p)
        except ValueError:raise ValueError('; '.join(tail) or 'Remote copy failed; completed files kept') from None
        finally:thread.join(timeout=2)
        return {'remote':data.get('target_remote'),'path':data.get('target_path')}

    def target(self,name,path=''):
        if not isinstance(name,str) or name not in self.names():raise ValueError('Select a configured rclone remote')
        if self.questions.get(name):raise ValueError('Finish configuring this remote in Settings → Rclone first')
        return name+':'+relative(path)

    def browse(self,name,path=''):
        target=self.target(name,path)
        entries=self.run('lsjson',target,'--dirs-only','--max-depth','1')
        # Never allow provider-returned ../ or a remote selector to become a link.
        folders=[e['Name'] for e in entries if isinstance(e,dict) and e.get('IsDir') and isinstance(e.get('Name'),str) and '/' not in e['Name'] and e['Name'] not in ('.','..') and ':' not in e['Name'] and '\\' not in e['Name']]
        return {'remote':name,'path':relative(path),'folders':sorted(folders,key=str.casefold)}

    def mkdir(self,name,path):
        if not relative(path):raise ValueError('Enter a folder name')
        self.run('mkdir',self.target(name,path),json_output=False)
        return {'ok':True}

    def providers(self):
        result=self.run('config','providers')
        return [{'name':p['Name'],'description':p.get('Description',p['Name'])} for p in (result if isinstance(result,list) else result['providers'])]

    def configure(self,data):
        name=data.get('name','')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',name):raise ValueError('Use a short remote name containing letters, numbers, dots, hyphens or underscores')
        with self.lock:
            if data.get('action')=='resume':
                response=self.responses.get(name)
                if not response or not response.get('State'):raise ValueError('No unfinished setup for this remote')
                return {'name':name,'done':False,'option':response.get('Option',{}),'error':response.get('Error','')}
            if data.get('action')=='start':
                if name in self.names():raise ValueError('Remote already exists; choose another name')
                provider=data.get('provider')
                if provider not in {p['name'] for p in self.providers()}:raise ValueError('Choose an installed rclone provider')
                response=self.run('config','create',name,provider,'--all','--non-interactive','--obscure')
            else:
                state=self.questions.get(name)
                if not state:raise ValueError('Configuration session expired; use a new remote name')
                answer=data.get('answer','')
                if not isinstance(answer,str) or len(answer)>200000:raise ValueError('Invalid configuration answer')
                response=self.run('config','update',name,'--continue','--non-interactive','--all','--state',state,'--result',answer,'--obscure')
            self.questions[name]=response.get('State','')
            option=response.get('Option') or {}
            # Keep headless authorization headless; never launch localhost OAuth on the laptop.
            if option.get('Name')=='config_is_local':
                response=self.run('config','update',name,'--continue','--non-interactive','--all','--state',response['State'],'--result','false')
                self.questions[name]=response.get('State','');option=response.get('Option') or {}
            self.responses[name]=response;self.save_sessions()
            return {'name':name,'done':not bool(response.get('State')),'option':option,'error':response.get('Error','')}

    def save_sessions(self):
        tmp=self.session_path.with_suffix('.tmp');tmp.write_text(json.dumps(self.responses));tmp.chmod(0o600);os.replace(tmp,self.session_path)

    def delete(self,name,confirm=False):
        if self.desk.remote_servers.active():raise ValueError('Stop remote streaming before removing a remote')
        if any(j['kind']=='remote-copy' and j['status'] in ('running','queued') for j in self.desk.tasks.public()):raise ValueError('Wait for remote copies to finish')
        if confirm is not True or name not in self.names():raise ValueError('Confirm removing this configured remote')
        if self.desk.rows("SELECT id FROM jobs WHERE remote=? AND status IN ('running','queued','retry','blocked','paused')",(name,)):
            raise ValueError('Cancel or remove downloads using this remote first')
        if any(j['kind']=='backup' and j['status'] in ('running','queued') for j in self.desk.tasks.public()):raise ValueError('Wait for backups to finish before removing a remote')
        schedule=self.desk.get_setting('backup_schedule',{})
        if schedule.get('enabled') and schedule.get('remote')==name:raise ValueError('Disable the backup schedule using this remote first')
        with self.lock:
            config=self.run('config','dump')
            for other,entry in config.items():
                if other!=name and any(re.search(r'(?:^|[ =])'+re.escape(name)+':',str(entry.get(k,''))) for k in ('remote','remotes','upstreams')):
                    raise ValueError('Another remote depends on this one: '+other)
            self.run('config','delete',name,json_output=False)
            self.questions.pop(name,None);self.responses.pop(name,None);self.save_sessions()
        return {'ok':True}

    def read_target(self,name,path=''):
        target=self.target(name,path);config=self.run('config','dump')
        def check(remote,seen):
            if remote in seen:raise ValueError('Circular remote configuration')
            entry=config.get(remote,{})
            if entry.get('type')=='cache':raise ValueError('Use the underlying remote or crypt remote; the legacy cache backend writes to disk')
            for key in ('remote','remotes','upstreams'):
                for ref in str(entry.get(key,'')).split():
                    if ':' in ref:
                        child=ref.split(':',1)[0].split('=',1)[-1]
                        if child in config:check(child,seen|{remote})
        check(name,set());return target

    def streaming_target(self,name,path,size=None):
        target=self.target(name,path)
        # Reject wrappers that could stage media on local disk. Remote credentials
        # remain private; dump is inspected here and is never returned to the UI.
        config=self.run('config','dump')
        def check(remote,seen):
            if remote in seen:raise ValueError('Circular remote configuration')
            entry=config.get(remote,{})
            if entry.get('type') in ('local','cache') or not entry.get('type'):raise ValueError('Direct cloud downloads require a remote backend without local caching')
            for key in ('remote','remotes','upstreams'):
                if key not in entry:continue
                refs=entry[key].split() if key!='remote' else [entry[key]]
                for ref in refs:
                    if ':' not in ref or ref.startswith(':'):raise ValueError('This wrapper uses a local or inline backend; direct streaming is unavailable')
                    check(ref.split(':',1)[0].split('=',1)[-1],seen|{remote})
        check(name,set())
        features=self.run('backend','features',name+':').get('Features',{})
        if size is None and not features.get('PutStream'):raise ValueError('This remote requires a known file size. Streaming this source would require local staging, which is disabled.')
        return target

    def stream_args(self,name,path,size=None):
        target=self.streaming_target(name,path,size)
        args=['rcat',target,'--buffer-size','1M','--streaming-upload-cutoff','1M','--transfers','1','--checkers','1','--retries','1','--low-level-retries','3','--stats','1s','--stats-one-line','--use-json-log','--drive-chunk-size','8M','--s3-upload-concurrency','1','--s3-chunk-size','5M']
        if size is not None:args+=['--size',str(size)]
        return self.args(*args)
