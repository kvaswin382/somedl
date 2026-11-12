"""Shared, validated configuration; one location for operational limits."""
DEFAULTS = {
    'simultaneous_downloads': 1, 'browser_timeout': 90, 'browser_settle': 6,
    'browser_min_memory_mib': 350, 'browser_max_frames': 16, 'browser_candidates': 160,
    'refresh_attempts': 2, 'http_timeout': 30, 'auto_cookies': True, 'block_trackers': True,
    'telegram_bot_token':'', 'telegram_chat_id':'', 'telegram_admin_id':'',
    'telegram_snapshots':False, 'telegram_errors':False,
    'health_alerts':True, 'telegram_pin_critical':True,
    'alert_cpu_percent':90, 'alert_ram_percent':90, 'alert_disk_percent':90,
    'alert_temperature_c':80, 'alert_sustain_seconds':60, 'alert_cooldown_minutes':15,
    'tracker_domains':'doubleclick.net,google-analytics.com,googlesyndication.com,exoclick.com,popads.net',
}
RANGES = {'simultaneous_downloads':(1,20), 'browser_timeout':(20,180), 'browser_settle':(2,15),
          'browser_min_memory_mib':(200,1000), 'browser_max_frames':(1,32), 'browser_candidates':(20,300),
          'refresh_attempts':(0,5), 'http_timeout':(10,90),
          'alert_cpu_percent':(50,99), 'alert_ram_percent':(50,99), 'alert_disk_percent':(70,99),
          'alert_temperature_c':(50,100), 'alert_sustain_seconds':(10,300), 'alert_cooldown_minutes':(1,120)}


def checked(data):
    result={}
    for key,value in data.items():
        if key in RANGES:
            if isinstance(value,bool):raise ValueError('Invalid '+key)
            n=int(value);lo,hi=RANGES[key]
            if str(value).strip()!=str(n) or not lo<=n<=hi:raise ValueError(f'{key} must be {lo}–{hi}')
            result[key]=n
        elif key in ('auto_cookies','block_trackers','telegram_snapshots','telegram_errors','health_alerts','telegram_pin_critical'):
            if type(value) is not bool:raise ValueError(key+' must be true or false')
            result[key]=value
        elif key in ('telegram_bot_token','telegram_chat_id','telegram_admin_id','tracker_domains'):
            import re
            s=str(value).strip()
            if len(s)>2000 or '\n' in s or '\r' in s:raise ValueError('Invalid '+key)
            if key=='telegram_bot_token' and s and not re.fullmatch(r'\d+:[A-Za-z0-9_-]+',s):raise ValueError('Invalid Telegram bot token')
            if key in ('telegram_chat_id','telegram_admin_id') and s and not re.fullmatch(r'-?\d+',s):raise ValueError('Use the numeric Telegram chat/user ID')
            if key=='tracker_domains' and any(not re.fullmatch(r'[a-z0-9.-]+',v.strip().lower()) for v in s.split(',') if v.strip()):raise ValueError('Use comma-separated hostnames')
            result[key]=s
    return result


# The UI consumes these ranges; validation and controls share one source.
TRANSFER_RANGES={'media_kib':(0,100000),'torrent_down_kib':(0,100000),
    'torrent_up_kib':(1,100000),'reserve_gib':(1,100),'playlist_limit':(1,500),
    'retry_count':(0,8),'seed_ratio':(0,10)}

def schema():
    return {key:{'min':lo,'max':hi,'step':0.1 if key=='seed_ratio' else 1}
            for key,(lo,hi) in {**RANGES,**TRANSFER_RANGES}.items()}
