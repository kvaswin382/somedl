"""Optional Telegram diagnostics. Never include cookies or signed URL queries."""
import json
import re
import urllib.request
import uuid
from urllib.parse import urlsplit


def sanitized(text):
    text=re.sub(r'https?://[^\s<>\"\']+',lambda m: urlsplit(m[0]).scheme+'://'+(urlsplit(m[0]).hostname or '')+urlsplit(m[0]).path, str(text))
    text=re.sub(r'(?i)(token|cookie|authorization|sessionid|csrf)[=: ]+\S+',r'\1=[redacted]',text)
    return text[:3000]


def send(settings,message,photo=None,*,critical=False):
    token=settings.get('telegram_bot_token','')
    keys=('telegram_chat_id','telegram_admin_id') if critical else ('telegram_chat_id',)
    ids=list(dict.fromkeys(str(settings.get(k,'')).strip() for k in keys if settings.get(k)))
    if not token or not ids:return {'ok':False,'errors':['Configure a bot token and '+('a group or admin ID' if critical else 'a group chat ID')]}
    # Critical messages contain trusted, escaped system metrics only; never photos.
    if critical:photo=None
    errors=[];warnings=[]
    def call(method,fields):
        req=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,
            json.dumps(fields).encode(),{'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=12) as response:
            result=json.load(response)
        if not result.get('ok'):raise ValueError('Telegram rejected the request')
        return result
    for cid in ids:
        try:
            fields={'chat_id':cid,'caption' if photo else 'text':message[:3000] if critical else sanitized(message)[:950 if photo else 3000]}
            if critical:fields['parse_mode']='HTML'
            if photo:
                boundary='zedair'+uuid.uuid4().hex
                data=b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k,v in fields.items())
                data+=f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="page.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()+photo+f'\r\n--{boundary}--\r\n'.encode()
                req=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendPhoto',data,{'Content-Type':'multipart/form-data; boundary='+boundary})
                with urllib.request.urlopen(req,timeout=12) as response:result=json.load(response)
                if not result.get('ok'):raise ValueError('Telegram rejected the message')
            else:result=call('sendMessage',fields)
        except Exception as exc:
            # Exception strings may contain the bot token. Return only safe types/codes.
            errors.append(f'{cid}: {type(exc).__name__}'+(f' HTTP {exc.code}' if hasattr(exc,'code') else ''))
            continue
        if critical and settings.get('telegram_pin_critical') and cid==str(settings.get('telegram_chat_id')):
            mid=result.get('result',{}).get('message_id')
            if mid:
                try:call('pinChatMessage',{'chat_id':cid,'message_id':mid,'disable_notification':False})
                except Exception:warnings.append('Alert delivered; could not pin it. Give the bot permission to pin group messages.')
    return {'ok':not errors,'errors':errors,'warnings':warnings}
