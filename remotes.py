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


def relative(value):
    if not isinstance(value,str) or len(value)>1024 or any(ord(c)<32 for c in value) or '\\' in value or ':' in value or value.startswith('/'):
        raise ValueError('Use a path inside the selected remote')
    bits=value.rstrip('/').split('/') if value else []
    if any(b in ('','.','..') for b in bits):raise ValueError('Use a path inside the selected remote')
    return '/'.join(bits)


class Remotes:
    def __init__(self,desk):
        self.desk=desk;self.config=desk.state/'rclone.conf';self.lock=threading.RLock();self.questions={}
        if self.config.is_symlink():raise ValueError('rclone config must not be a symlink')
        self.config.touch(mode=0o600,exist_ok=True);self.config.chmod(0o600)

    def args(self,*args):
        binary=shutil.which('rclone')
        if not binary:raise ValueError('rclone is not installed. Run the project upgrade script.')
        return [binary,'--config',str(self.config),'--contimeout','15s','--timeout','60s',*args]

    def run(self,*args,json_output=True,timeout=90):
        with self.lock:
            p=subprocess.run(self.args(*args),capture_output=True,text=True,timeout=timeout)
            self.config.chmod(0o600)
        if p.returncode:raise ValueError('rclone: '+sanitized(p.stderr)[-1800:])
        if len(p.stdout)>16*1024*1024:raise ValueError('Remote listing is too large; choose a smaller folder')
        return json.loads(p.stdout or '{}') if json_output else p.stdout

    def names(self):
        return [s[:-1] for s in self.run('listremotes',json_output=False).splitlines() if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_. -]{0,63}:',s)]

    def target(self,name,path=''):
        if not isinstance(name,str) or name not in self.names():raise ValueError('Select a configured rclone remote')
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
            return {'name':name,'done':not bool(response.get('State')),'option':option,'error':response.get('Error','')}

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
