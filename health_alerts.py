"""Cheap, deterministic system-alert decisions; no network or subprocesses."""
from html import escape
import math


class Monitor:
    def __init__(self):
        self.active={}
        self.sent={}

    def sample(self,snapshot,settings,now):
        if not settings.get('health_alerts') or not settings.get('telegram_bot_token'):
            self.active.clear()
            return []
        sustain=settings['alert_sustain_seconds']
        conditions=[
            ('cpu','CPU usage',snapshot.get('cpu_percent'),settings['alert_cpu_percent'],'%',sustain,False),
            ('ram','RAM usage',(snapshot.get('memory') or {}).get('percent'),settings['alert_ram_percent'],'%',sustain,False),
            ('disk','System disk usage',(snapshot.get('system_disk') or {}).get('percent'),settings['alert_disk_percent'],'%',sustain,False),
            ('temperature','CPU temperature',snapshot.get('temperature_c'),settings['alert_temperature_c'],'°C',0,False),
            ('ssd','External SSD free space',(snapshot['ssd']['available']/1024**3) if (snapshot.get('ssd') or {}).get('available') is not None else None,settings['reserve_gib'],'GiB',0,True),
        ]
        messages=[]
        for key,label,value,limit,unit,delay,low in conditions:
            if value is None or not math.isfinite(value) or not (value<limit if low else value>limit):
                self.active.pop(key,None)
                continue
            signature=(limit,delay)
            first,old=self.active.get(key,(now,signature))
            if old!=signature:first=now
            self.active[key]=(first,signature)
            if now-first<delay or now-self.sent.get(key,float('-inf'))<settings['alert_cooldown_minutes']*60:continue
            self.sent[key]=now
            duration=f'\nSustained for {int(now-first)} seconds.' if delay else ''
            messages.append('🚨 <b>Zedair · system alert</b>\n\n'
                f'<b>{escape(label)}: {value:.1f} {unit}</b>\n'
                f'Threshold: {"below" if low else "above"} {limit:g} {unit}.{duration}\n\n'
                'Open Settings → System usage to check the laptop.')
        return messages
