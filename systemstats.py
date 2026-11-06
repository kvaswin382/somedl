"""Read-only Linux host metrics. No external agent, subprocess or busy sampling loop."""
import os
from pathlib import Path
import shutil
import threading
import time


def memory_stats(text):
    values={}
    for line in text.splitlines():
        key,_,rest=line.partition(':')
        try:values[key]=int(rest.split()[0])*1024
        except (ValueError,IndexError):continue
    total=values.get('MemTotal',0)
    if not total:return None,None
    available=values.get('MemAvailable',values.get('MemFree',0)+values.get('Buffers',0)+values.get('Cached',0))
    available=max(0,min(total,available));used=total-available
    swap=values.get('SwapTotal',0);swap_used=max(0,min(swap,swap-values.get('SwapFree',0)))
    return ({'total':total,'used':used,'available':available,'percent':100*used/total},
            {'total':swap,'used':swap_used,'percent':100*swap_used/swap if swap else 0})


def cpu_ticks(text):
    fields=text.splitlines()[0].split()
    if fields[0]!='cpu' or len(fields)<5:raise ValueError('CPU counters unavailable')
    values=[int(x) for x in fields[1:9]]  # guest times already included in user/nice
    return sum(values),values[3]+(values[4] if len(values)>4 else 0)


def disk_stats(path):
    if path is None:return None
    try:
        total,used,free=shutil.disk_usage(path)
        return {'total':total,'used':used,'available':free,'percent':100*used/total if total else 0}
    except OSError:return None


class SystemStats:
    def __init__(self,proc='/proc',thermal='/sys/class/thermal'):
        self.proc=Path(proc);self.thermal=Path(thermal);self.lock=threading.Lock()
        self.last=None;self.last_time=0;self.cpu=None
        self._cpu(time.monotonic())

    def _cpu(self,stamp):
        if stamp-self.last_time<1:return self.cpu
        try:current=cpu_ticks((self.proc/'stat').read_text())
        except (OSError,ValueError,IndexError):self.last=None;self.cpu=None;return None
        if self.last:
            total=current[0]-self.last[0];idle=current[1]-self.last[1]
            self.cpu=max(0,min(100,100*(total-idle)/total)) if total>0 and idle>=0 else None
        self.last=current;self.last_time=stamp
        return self.cpu

    def snapshot(self,ssd=None):
        with self.lock:
            cpu=self._cpu(time.monotonic())
            try:memory,swap=memory_stats((self.proc/'meminfo').read_text())
            except OSError:memory=swap=None
            try:uptime=float((self.proc/'uptime').read_text().split()[0])
            except (OSError,ValueError,IndexError):uptime=None
            try:load=list(os.getloadavg())
            except OSError:load=None
            temperatures=[]
            for zone in self.thermal.glob('thermal_zone*'):
                try:
                    kind=(zone/'type').read_text().strip().lower()
                    if not any(x in kind for x in ('cpu','x86_pkg','soc')):continue
                    value=float((zone/'temp').read_text())/1000
                    if -20<=value<=130:temperatures.append(value)
                except (OSError,ValueError):continue
            return {'sampled_at':time.time(),'cpu_percent':cpu,'cores':os.cpu_count() or 1,
                    'memory':memory,'swap':swap,'system_disk':disk_stats('/'),'ssd':disk_stats(ssd),
                    'uptime_seconds':uptime,'load':load,'temperature_c':max(temperatures) if temperatures else None}
