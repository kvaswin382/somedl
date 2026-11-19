"""Bounded activity journal and cooperative, registered background workers."""
import collections
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from notifications import sanitized


def initialize(desk):
    desk.write('CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, time REAL, event TEXT, detail TEXT)')


def record(desk,event,detail=''):
    # Call sites pass identifiers/outcomes, never request bodies, credentials or cookies.
    text=sanitized(str(detail))[:2000]
    with desk.lock:
        desk.db.execute('INSERT INTO activity(time,event,detail) VALUES(?,?,?)',(time.time(),str(event)[:80],text))
        desk.db.execute('DELETE FROM activity WHERE id <= (SELECT max(id)-10000 FROM activity)')
        desk.db.commit()


class Tasks:
    def __init__(self,desk):
        self.desk=desk;self.lock=threading.RLock();self.jobs={};self.pending=collections.deque()
        self.wake=threading.Event();self.uploads={};self.closing=False

    def submit(self,kind,work):
        with self.desk.lock,self.lock:
            if self.closing or self.desk.get_setting('reboot_requested_at'):raise ValueError('Service is stopping or rebooting')
            if sum(j['status'] in ('queued','running') for j in self.jobs.values())>=8:raise ValueError('Background queue is full')
            key=uuid.uuid4().hex
            self.jobs[key]={'id':key,'kind':kind,'status':'queued','created':time.time(),'bytes':0,'detail':'Waiting','cancel':threading.Event(),'process':None}
            self.pending.append((key,work));self.wake.set()
        record(self.desk,kind+' queued',key)
        return {'id':key}

    def public(self,detail=None):
        with self.lock:return [{k:v for k,v in j.items() if k not in ('cancel','process') and (k!='result' or detail==j['id'])} for j in self.jobs.values()]

    def busy(self):
        with self.lock:
            cutoff=time.time()-3600
            return any(j['status']=='running' for j in self.jobs.values()) or any(u['updated']>cutoff for u in self.uploads.values())

    def check(self,job):
        if self.desk.stop.is_set() or job['cancel'].is_set():raise InterruptedError('Stopped safely; completed files preserved')

    def cancel(self,key):
        with self.lock:
            j=self.jobs.get(key)
            if not j or j['status'] not in ('queued','running'):raise ValueError('Worker is no longer active')
            j['cancel'].set()
            # A cooperative worker handles child shutdown and cleanup. No PID from the browser is used.
        record(self.desk,'worker stop requested',key)

    def loop(self):
        while not self.desk.stop.is_set():
            if self.desk.get_setting('reboot_requested_at'):
                self.desk.stop.wait(1);continue
            with self.lock:item=self.pending.popleft() if self.pending else None
            if not item:self.wake.wait(2);self.wake.clear();continue
            key,work=item;j=self.jobs[key]
            try:
                self.check(j);j.update(status='running',detail='Starting')
                result=work(j);self.check(j);j.update(status='done',detail='Completed',result=result)
            except InterruptedError as exc:j.update(status='cancelled',detail=str(exc))
            except Exception as exc:j.update(status='error',detail=sanitized(str(exc))[:2000])
            finally:
                j['process']=None;j['finished']=time.time();record(self.desk,j['kind']+' '+j['status'],j['detail'])
                with self.lock:
                    for old in list(self.jobs):
                        if len(self.jobs)<=50:break
                        if self.jobs[old]['status'] not in ('running','queued'):del self.jobs[old]

    def process(self,job,args,**kwargs):
        import subprocess
        self.check(job)
        p=subprocess.Popen(args,start_new_session=True,**kwargs);job['process']=p
        return p

    def wait(self,job,p):
        try:
            while p.poll() is None:
                self.check(job);time.sleep(.25)
            self.check(job)
            if p.returncode:raise ValueError('Worker exited with code '+str(p.returncode))
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try:p.wait(timeout=15)
                except TimeoutError:raise
                except Exception:
                    os.killpg(p.pid,signal.SIGKILL);p.wait()
            raise
        finally:job['process']=None


def processes(desk):
    # Do not expose arguments: cookie values and signed URLs can be command arguments.
    managed={p.pid:('download',key) for key,p in desk.processes.items() if p.poll() is None}
    with desk.tasks.lock:
        for key,j in desk.tasks.jobs.items():
            p=j.get('process')
            if p and p.poll() is None:managed[p.pid]=(j['kind'],key)
    result=[];ticks=os.sysconf('SC_CLK_TCK');stamp=time.monotonic()
    previous=getattr(desk,'process_samples',{});samples={}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            raw=(path/'stat').read_text();end=raw.rfind(')');fields=raw[end+2:].split();pid=int(path.name)
            cpu=(int(fields[11])+int(fields[12]))/ticks;start=fields[19];rss=int(fields[21])*os.sysconf('SC_PAGE_SIZE')
            old=previous.get(pid);percent=max(0,100*(cpu-old[1])/(stamp-old[0])) if old and old[2]==start and stamp>old[0] else 0
            samples[pid]=(stamp,cpu,start);owner=managed.get(pid)
            result.append({'pid':pid,'name':raw[raw.index('(')+1:end],'ram':rss,'cpu':round(percent,1),'worker':owner[1] if owner else '', 'kind':owner[0] if owner else ''})
        except (OSError,ValueError,IndexError):continue
    desk.process_samples=samples
    return sorted(result,key=lambda x:x['ram'],reverse=True)
