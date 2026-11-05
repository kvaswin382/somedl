"""Bounded server-side HTTP speed estimate. No payload is written to disk."""
import http.client
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

HOST = 'speed.cloudflare.com'

def measure(emit, connection_factory=http.client.HTTPSConnection):
    connection = connection_factory(HOST, timeout=12)
    deadline = time.monotonic()+75
    headers = {'User-Agent':'Zedair-Download-Desk/1.1', 'Cache-Control':'no-cache', 'Accept-Encoding':'identity'}
    transferred = 0
    def request(size, upload=False):
        nonlocal transferred
        if time.monotonic()>deadline:
            raise TimeoutError('Test time limit reached')
        body = b'0'*size if upload else None  # largest upload is 8 MiB
        start = time.monotonic()
        path = '/__up' if upload else '/__down?bytes='+str(size)+'&r='+str(time.time_ns())
        connection.request('POST' if upload else 'GET',path,body=body,headers=headers)
        response = connection.getresponse()
        ttfb = (time.monotonic()-start)*1000
        if response.status!=200:
            raise ValueError('Speed-test endpoint returned HTTP '+str(response.status))
        count = 0
        while block := response.read(65536):
            count += len(block)
            if count > size+65536 or time.monotonic()>deadline:
                raise TimeoutError('Speed-test response exceeded its limit')
        if not upload and count!=size:
            raise ValueError('Incomplete test response; no speed result was accepted')
        transferred += size if upload else count
        return ttfb, size*8/max(time.monotonic()-start,.000001)/1_000_000
    try:
        emit({'phase':'latency','detail':'Measuring HTTP latency from the laptop…'})
        request(0) # warm TLS connection; excluded from latency median
        latency=[request(0)[0] for _ in range(5)]
        emit({'latency_ms':round(statistics.median(latency),1), 'jitter_ms':round(statistics.mean(abs(b-a) for a,b in zip(latency,latency[1:])),1)})
        emit({'phase':'download','detail':'Downloading test data to the laptop’s memory…'})
        request(8*1024*1024)
        _, down=request(24*1024*1024)
        emit({'download_mbps':round(down,2),'phase':'upload','detail':'Uploading generated test data from the laptop…'})
        request(1024*1024,True)
        _, up=request(8*1024*1024,True)
        emit({'upload_mbps':round(up,2),'transferred_bytes':transferred,'phase':'done','detail':'Test complete. Single-connection HTTP estimate to Cloudflare.'})
    finally:
        connection.close()

class SpeedTest:
    def __init__(self, desk):
        self.desk=desk
        self.lock=threading.RLock()
        self.result={'status':'idle','phase':'idle','detail':'Ready to test this laptop'}
        self.process=None
        self.thread=None
        self.cancelled=False
        self.last_start=0

    def snapshot(self):
        with self.lock:
            return dict(self.result,history=self.desk.get_setting('speed_history',[]))

    def start(self):
        with self.lock:
            if self.result['status']=='running':
                raise ValueError('A speed test is already running on the laptop')
            if time.monotonic()-self.last_start<30:
                raise ValueError('Wait 30 seconds between speed tests')
            self.last_start=time.monotonic()
            self.cancelled=False
            self.result={'status':'running','phase':'starting','started':time.time(),'detail':'Starting test on the Zedair laptop…'}
            self.thread=threading.Thread(target=self.run,daemon=True)
            self.thread.start()

    def cancel(self):
        with self.lock:
            self.cancelled=True
            if self.process and self.process.poll() is None:
                self.process.terminate()

    def run(self):
        timer=None
        try:
            with self.lock:
                if self.cancelled:
                    self.result.update(status='cancelled',phase='cancelled',detail='Test cancelled')
                    return
                self.process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=dict(os.environ,PYTHONUNBUFFERED='1'))
                process=self.process
            timer=threading.Timer(90,process.kill);timer.daemon=True;timer.start()
            for line in process.stdout:
                try:
                    update=json.loads(line)
                    with self.lock:
                        self.result.update(update)
                except ValueError:
                    pass
            process.stdout.close()
            code=process.wait()
            with self.lock:
                if self.cancelled:
                    self.result.update(status='cancelled',phase='cancelled',detail='Test cancelled')
                elif code==0 and self.result.get('phase')=='done':
                    self.result.update(status='done',finished=time.time())
                    history=self.desk.get_setting('speed_history',[])
                    self.desk.set_setting('speed_history',([dict(self.result)]+history)[:10])
                else:
                    self.result.update(status='error',phase='error',detail=self.result.get('error','Test failed or timed out. Check the laptop’s connection and try again.'))
        except Exception:
            with self.lock:
                self.result.update(status='error',phase='error',detail='Could not start the laptop speed test')
        finally:
            if timer:timer.cancel()
            with self.lock:self.process=None

    def close(self):
        self.cancel()
        if self.thread:self.thread.join(timeout=5)

if __name__=='__main__':
    try:
        measure(lambda value: print(json.dumps(value),flush=True))
    except Exception as exc:
        print(json.dumps({'error':str(exc)[:300]}),flush=True)
        sys.exit(1)
