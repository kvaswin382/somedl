"""In-process cron calendar in Asia/Kolkata; no shell expressions or crontab edits."""
import datetime
import re
from zoneinfo import ZoneInfo
ZONE=ZoneInfo('Asia/Kolkata')


def fields(expression):
    parts=str(expression).split()
    if len(parts)!=5:raise ValueError('Cron needs five fields: minute hour day month weekday')
    output=[]
    for text,(lo,hi) in zip(parts,((0,59),(0,23),(1,31),(1,12),(0,6))):
        values=set()
        for part in text.split(','):
            if not re.fullmatch(r'(?:\*|\d+(?:-\d+)?)(?:/\d+)?',part):raise ValueError('Use numeric cron fields, ranges, commas and steps')
            token,*step=part.split('/');stride=int(step[0]) if step else 1
            if stride<1 or stride>hi+1:raise ValueError('Invalid cron step')
            if token=='*':a,b=lo,hi
            elif '-' in token:a,b=map(int,token.split('-'))
            else:a=b=int(token)
            if not lo<=a<=b<=hi:raise ValueError('Cron field outside its range')
            values.update(range(a,b+1,stride))
        output.append(values)
    return parts,output


def matches(expression,when):
    parts,sets=fields(expression);minute,hour,day,month,weekday=sets
    dt=when.astimezone(ZONE)
    dom=dt.day in day;dow=(dt.weekday()+1)%7 in weekday
    calendar=(dom or dow) if parts[2]!='*' and parts[4]!='*' else dom and dow
    return dt.minute in minute and dt.hour in hour and dt.month in month and calendar


def save(desk,data):
    kind=data.get('kind')
    if kind not in ('backup','reboot'):raise ValueError('Unknown schedule')
    enabled=data.get('enabled') is True;cron=str(data.get('cron','0 3 * * *'))
    fields(cron)
    value={'enabled':enabled,'cron':cron,'timezone':'Asia/Kolkata'}
    if kind=='backup':
        value.update(backup_options(desk,data))
    desk.set_setting(kind+'_schedule',value)
    from activities import record
    record(desk,kind+' schedule changed',cron if enabled else 'disabled')
    return value


def backup_options(desk,data):
    name=data.get('remote');path=data.get('path','');desk.remotes.target(name,path)
    scope=data.get('scope','system');days=data.get('days',30)
    if scope not in ('system','ssd','both') or type(days) is not int or not 1<=days<=3650:raise ValueError('Choose backup scope and retention of 1–3650 days')
    return {'remote':name,'path':path,'scope':scope,'days':days}


def busy(desk):
    # Include seed uploads, uploads to folders, backup streams, speed tests and
    # workers that are still exiting after pause.
    if desk.workers or desk.processes or desk.tasks.busy() or desk.speedtest.snapshot().get('status')=='running':return True
    if desk.torrent_error:return True  # Cannot prove idle while RPC is unavailable.
    return any(t.get('status') in (1,2,3,4,5,6) for t in desk.torrents)


def loop(desk):
    from activities import record
    from backups import backup
    import subprocess
    last_thumb=0
    while not desk.stop.wait(15):
        if desk.get_setting('reboot_requested_at') and __import__('time').time()-desk.get_setting('reboot_requested_at')>60:
            desk.set_setting('hold',desk.get_setting('reboot_restore_hold',False));desk.write("DELETE FROM settings WHERE key IN ('reboot_restore_hold','reboot_requested_at')");record(desk,'reboot did not occur','Queue state restored; inspect system control status')
        when=datetime.datetime.now(ZONE);key=when.strftime('%Y-%m-%dT%H:%M')
        for kind in ('backup','reboot'):
            s=desk.get_setting(kind+'_schedule',{})
            if not s.get('enabled') or not matches(s['cron'],when) or desk.get_setting(kind+'_last_tick')==key:continue
            desk.set_setting(kind+'_last_tick',key)
            try:
                if kind=='backup':
                    if any(j['kind']=='backup' and j['status'] in ('queued','running') for j in desk.tasks.public()):
                        record(desk,'backup skipped','Previous backup still active');continue
                    desk.tasks.submit('backup',lambda job,s=s:backup(desk,job,s))
                else:
                    with desk.lock:
                        if busy(desk):record(desk,'reboot skipped','Downloads, uploads, backups or torrents active, or torrent state unavailable');continue
                        old=desk.get_setting('hold',False);desk.set_setting('hold',True)
                        try:
                            desk.set_setting('reboot_restore_hold',old);desk.set_setting('reboot_requested_at',when.timestamp())
                            record(desk,'scheduled reboot','Asia/Kolkata '+key)
                            p=subprocess.run(['/usr/bin/systemctl','--no-ask-password','start','--no-block','zedair-reboot.service'],capture_output=True,text=True,timeout=10)
                            if p.returncode:raise ValueError('Reboot integration unavailable; run upgrade.sh')
                            # Hold is restored by startup, after the OS has restarted.
                            desk.set_setting('reboot_restore_hold',old)
                        except Exception:
                            desk.set_setting('hold',old);desk.write("DELETE FROM settings WHERE key IN ('reboot_restore_hold','reboot_requested_at')");raise
            except Exception as exc:record(desk,kind+' schedule failed',str(exc))
        # Existing/uploaded/newly downloaded videos are discovered without a
        # large permanent queue or one FFmpeg process per request.
        if when.timestamp()-last_thumb>900 and desk.storage(reserve=False)['ready'] and not desk.tasks.busy():
            from storage_tools import queue_thumbnails
            if not any(j['kind']=='thumbnails' and j['status'] in ('running','queued') for j in desk.tasks.public()):
                queue_thumbnails(desk);last_thumb=when.timestamp()
