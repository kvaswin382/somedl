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


def send(settings,message,photo=None):
    token=settings.get('telegram_bot_token','')
    ids=list(dict.fromkeys(str(settings.get(k,'')).strip() for k in ('telegram_chat_id','telegram_admin_id') if settings.get(k)))
    if not token or not ids:return {'ok':False,'error':'Configure a bot token and a numeric recipient ID'}
    errors=[]
    for cid in ids:
        try:
            fields={'chat_id':cid, 'caption' if photo else 'text':sanitized(message)[:950 if photo else 3000]}
            if photo:
                boundary='zedair'+uuid.uuid4().hex
                data=b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k,v in fields.items())
                data+=f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="page.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()+photo+f'\r\n--{boundary}--\r\n'.encode()
                mime='multipart/form-data; boundary='+boundary
            else:data=json.dumps(fields).encode();mime='application/json'
            req=urllib.request.Request('https://api.telegram.org/bot'+token+('/sendPhoto' if photo else '/sendMessage'),data,{'Content-Type':mime})
            with urllib.request.urlopen(req,timeout=12) as response:
                if not json.load(response).get('ok'):raise ValueError('Telegram rejected the message')
        except Exception as exc:
            # urllib exception text can contain the bot token. Never return it.
            errors.append(f'{cid}: {type(exc).__name__}'+(f' HTTP {exc.code}' if hasattr(exc,'code') else ''))
    return {'ok':not errors,'errors':errors}
