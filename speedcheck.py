"""Run official Ookla Speedtest on the laptop; never derive speeds from browser traffic."""
import csv
import io
import json
import math
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

ENGINE = 'ookla'
HISTORY_LIMIT = 100


def cli_path():
    for path in (Path('/opt/zedair/bin/speedtest'), Path('/usr/bin/speedtest')):
        if path.is_file() and os.access(path, os.X_OK):
            try:
                version = subprocess.run([str(path),'--version'], capture_output=True, text=True, timeout=5)
                if version.returncode==0 and 'ookla' in (version.stdout+version.stderr).lower():
                    return str(path)
            except (OSError,subprocess.TimeoutExpired):
                pass
    return None


def number(value, name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError('Invalid '+name+' in speed-test result')
    return value


def parse_result(data):
    """Ookla JSON bandwidth is bytes/second, elapsed is milliseconds, ping is ms."""
    if data.get('type')!='result':
        raise ValueError('The speed test did not produce a final result')
    down,up,ping=data.get('download',{}),data.get('upload',{}),data.get('ping',{})
    result={
        'download_mbps':round(number(down.get('bandwidth'),'download bandwidth')*8/1_000_000,2),
        'upload_mbps':round(number(up.get('bandwidth'),'upload bandwidth')*8/1_000_000,2),
        'latency_ms':round(number(ping.get('latency'),'ping'),2),
        'jitter_ms':round(number(ping.get('jitter'),'jitter'),2),
        'download_bytes':number(down.get('bytes'),'download bytes'),
        'upload_bytes':number(up.get('bytes'),'upload bytes'),
        'download_elapsed_ms':number(down.get('elapsed'),'download duration'),
        'upload_elapsed_ms':number(up.get('elapsed'),'upload duration'),
        'server':str(data.get('server',{}).get('name','Unknown'))[:200],
        'location':str(data.get('server',{}).get('location',''))[:200],
        'server_id':str(data.get('server',{}).get('id',''))[:30],
        'isp':str(data.get('isp',''))[:200],
        'engine':ENGINE,
    }
    if not result['download_bytes'] or not result['upload_bytes'] or not result['download_elapsed_ms'] or not result['upload_elapsed_ms']:
        raise ValueError('The speed test returned an empty or incomplete transfer')
    if data.get('packetLoss') is not None:
        result['packet_loss']=number(data['packetLoss'],'packet loss')
    url=str(data.get('result',{}).get('url',''))
    if re.fullmatch(r'https://www\.speedtest\.net/result/c/[a-zA-Z0-9-]+',url):
        result['result_url']=url
    # Do not persist public/private IPs, MAC addresses or raw engine output.
    return result


def history_csv(history):
    out=io.StringIO(newline='')
    fields=['finished','status','engine','download_mbps','upload_mbps','latency_ms','jitter_ms','server','location','isp','detail']
    writer=csv.writer(out);writer.writerow(fields)
    for row in history:
        values=[]
        for key in fields:
            value=row.get(key,'legacy-http' if key=='engine' else '')
            if key=='finished' and isinstance(value,(float,int)):
                value=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(value))
            if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):
                value="'"+value
            values.append(value)
        writer.writerow(values)
    return out.getvalue().encode('utf-8-sig')


class SpeedTest:
    def __init__(self,desk):
        self.desk=desk
        self.lock=threading.RLock()
        self.result={'status':'idle','phase':'idle','detail':'Ready to test the laptop’s internet connection'}
        self.binary=cli_path()
        self.process=None
        self.thread=None
        self.cancelled=False
        self.last_start=0

    def history(self):
        # Preserve old reports but explicitly label their different, unreliable method.
        return [dict(row,engine=row.get('engine','legacy-http'),id=row.get('id','legacy-'+str(i)))
                for i,row in enumerate(self.desk.get_setting('speed_history',[]))][:HISTORY_LIMIT]

    def snapshot(self):
        with self.lock:
            return dict(self.result,history=self.history(),installed=bool(self.binary),
                        consent=bool(self.desk.get_setting('ookla_consent',False)))

    def start(self,accept_terms=False):
        with self.lock:
            if self.result['status']=='running':
                raise ValueError('A speed test is already running on the laptop')
            if time.monotonic()-self.last_start<30:
                raise ValueError('Wait 30 seconds between speed tests')
            self.binary=self.binary or cli_path()
            if not self.binary:
                raise ValueError('Install the official engine on the laptop: sudo bash /opt/zedair/install-speedtest.sh')
            if not self.desk.get_setting('ookla_consent',False):
                if accept_terms is not True:
                    raise ValueError('Read and accept Ookla’s license, terms and privacy notice before testing')
                self.desk.set_setting('ookla_consent',{'accepted_at':time.time(),'engine':'Ookla CLI 1.2'})
            self.last_start=time.monotonic()
            self.cancelled=False
            self.result={'id':secrets.token_hex(8),'engine':ENGINE,'status':'running','phase':'starting','started':time.time(),
                         'detail':'Selecting a nearby test server…','progress':0}
            self.thread=threading.Thread(target=self.run,daemon=True)
            self.thread.start()

    def cancel(self):
        with self.lock:
            if self.result['status']!='running':return
            self.cancelled=True
            if self.process and self.process.poll() is None:self.process.terminate()

    def save_report(self):
        self.result['finished']=time.time()
        self.result['duration_seconds']=round(self.result['finished']-self.result['started'],1)
        report={k:v for k,v in self.result.items() if k not in ('live_mbps','progress')}
        self.desk.set_setting('speed_history',([report]+self.history())[:HISTORY_LIMIT])

    def run(self):
        timer=None
        final=None
        try:
            with self.lock:
                if self.cancelled:
                    self.result.update(status='cancelled',phase='cancelled',detail='Test cancelled')
                    self.save_report();return
                # Acceptance flags are used only after the user has checked the GUI agreement.
                args=[self.binary,'--accept-license','--accept-gdpr','--format=json','--progress=yes','--progress-update-interval=500']
                (self.desk.state/'speedtest-config').mkdir(mode=0o700,exist_ok=True)
                self.process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                    env=dict(os.environ,XDG_CONFIG_HOME=str(self.desk.state/'speedtest-config')))
                process=self.process
            timer=threading.Timer(180,process.kill);timer.daemon=True;timer.start()
            for raw in iter(lambda:process.stdout.readline(65536),b''):
                try:
                    data=json.loads(raw)
                    if not isinstance(data,dict):continue
                    kind=data.get('type')
                    with self.lock:
                        if kind=='result':
                            final=parse_result(data)
                        elif kind in ('ping','download','upload'):
                            part=data.get(kind,{})
                            phase={'ping':'latency','download':'download','upload':'upload'}[kind]
                            self.result.update(phase=phase,detail={'ping':'Measuring ping…','download':'Testing download…','upload':'Testing upload…'}[kind])
                            self.result['progress']=min(100,number(part.get('progress',0),'progress')*100)
                            if kind in ('download','upload'):
                                self.result['live_mbps']=round(number(part.get('bandwidth',0),'bandwidth')*8/1_000_000,2)
                            elif 'latency' in part:
                                self.result['latency_ms']=round(number(part['latency'],'ping'),2)
                except (ValueError,TypeError,KeyError):
                    continue
            process.stdout.close();code=process.wait()
            with self.lock:
                if self.cancelled:
                    self.result.update(status='cancelled',phase='cancelled',detail='Test cancelled')
                elif code==0 and final:
                    self.result.update(final,status='done',phase='done',detail='Test complete',progress=100)
                else:
                    self.result.update(status='error',phase='error',detail='Speed test failed or timed out. Check the laptop’s connection and the installed Ookla CLI. No completed speeds were saved.')
                self.save_report()
        except Exception:
            with self.lock:
                self.result.update(status='error',phase='error',detail='Could not run the official speed-test engine. Check its installation on the laptop.')
                self.save_report()
        finally:
            if timer:timer.cancel()
            with self.lock:self.process=None

    def close(self):
        self.cancel()
        if self.thread:self.thread.join(timeout=5)
