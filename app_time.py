"""Application calendars use a selected IANA zone; stored instants remain UTC."""
import datetime as dt
from zoneinfo import ZoneInfo, available_timezones

DEFAULT='Asia/Kolkata'

def checked(value):
    if not isinstance(value,str) or value not in available_timezones():
        raise ValueError('Choose a valid IANA timezone')
    return value

def zone(desk):
    return ZoneInfo(desk.get_setting('timezone',DEFAULT))

def summary(desk,period):
    tz=zone(desk);today=dt.datetime.now(tz).date()
    starts={'day':today,'week':today-dt.timedelta(days=today.weekday()),'month':today.replace(day=1),'year':today.replace(month=1,day=1)}
    if period not in starts:raise ValueError('Choose day, week, month or year')
    start=starts[period];since=start.isoformat();end=today.isoformat()
    # Old releases stored hourly UTC buckets. New samples use UTC minutes so
    # half/quarter-hour zone boundaries are correct without per-packet rows.
    def local(value):
        return dt.datetime.fromisoformat(value).replace(tzinfo=dt.timezone.utc).astimezone(tz).strftime('%Y-%m-%dT%H')
    lower=(dt.datetime.combine(start,dt.time(),tzinfo=tz).astimezone(dt.timezone.utc)-dt.timedelta(hours=1)).strftime('%Y-%m-%dT%H')
    upper=dt.datetime.combine(today+dt.timedelta(days=1),dt.time(),tzinfo=tz).astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M')
    with desk.lock:
        desk.flush_usage(force=True)
        desk.db.create_function('usage_local',1,local,deterministic=True)
        clause='hour>=? AND hour<? AND substr(usage_local(hour),1,10) BETWEEN ? AND ?'
        args=(lower,upper,since,end)
        domains=desk.rows('SELECT domain,sum(bytes) bytes,sum(uploaded) uploaded FROM usage_hourly WHERE '+clause+' GROUP BY domain ORDER BY bytes DESC',args)
        length=13 if period=='day' else 7 if period=='year' else 10
        points=desk.rows('SELECT substr(usage_local(hour),1,'+str(length)+') label,sum(bytes) bytes,sum(uploaded) uploaded FROM usage_hourly WHERE '+clause+' GROUP BY label',args)
    if period=='day':labels=[since+f'T{h:02}' for h in range(24)]
    elif period=='year':labels=[f'{today.year}-{m:02}' for m in range(1,13)]
    else:labels=[(start+dt.timedelta(days=n)).isoformat() for n in range((today-start).days+1)]
    values={r['label']:r for r in points}
    return {'period':period,'timezone':str(tz),'since':since,'domains':domains,
        'total':sum(r['bytes'] for r in domains),'uploaded':sum(r['uploaded'] for r in domains),
        'series':[{'label':k,'bytes':values.get(k,{}).get('bytes',0),'uploaded':values.get(k,{}).get('uploaded',0)} for k in labels],
        'note':'Transferred payload only; existing files and torrent verification are excluded. Retransfers count again. Browser traffic/protocol overhead excluded. Older hourly samples are assigned by bucket start; previous missing or incorrect measurements cannot be reconstructed.'}
