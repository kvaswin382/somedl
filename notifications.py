"""Optional Telegram diagnostics. Never include cookies or signed URL queries."""
import json
import re
import urllib.request
import urllib.error
import urllib.parse
import uuid
from urllib.parse import urlsplit


def sanitized(text):
    text=re.sub(r'https?://[^\s<>\"\']+',lambda m: urlsplit(m[0]).scheme+'://'+(urlsplit(m[0]).hostname or '')+urlsplit(m[0]).path, str(text))
    text=re.sub(r'(?i)(token|cookie|authorization|sessionid|csrf)[=: ]+\S+',r'\1=[redacted]',text)
    return text[:3000]


class TelegramError(Exception):
    """A bounded, sanitized Bot API error, never a request URL or raw exception."""
    def __init__(self, payload, token, code=None):
        self.code=payload.get('error_code',code)
        params=payload.get('parameters',{})
        self.parameters=params if isinstance(params,dict) else {}
        description=str(payload.get('description') or 'Telegram rejected the request')
        # Redact the exact credential before URL sanitization, including escaped forms.
        for secret in (token,urllib.parse.quote(token,safe='')):
            if secret:description=description.replace(secret,'[redacted]')
        description=re.sub(r'(?i)bot\d+:[A-Za-z0-9_-]+','bot[redacted]',description)
        try:description=sanitized(description)
        except ValueError:description='Telegram returned an invalid error description'
        self.description=' '.join(description.split())[:600]
        super().__init__(self.description)

    def explain(self):
        text=(f'HTTP {self.code} · ' if self.code else '')+self.description
        lower=self.description.lower()
        if 'chat not found' in lower:
            text+=' · Check the group ID and that this bot is a member of that group.'
        elif self.code==401:
            text+=' · Check the saved bot token.'
        elif self.code==403 or any(x in lower for x in ('not enough rights','chat_write_forbidden','have no rights')):
            text+=' · Allow this bot to send messages in the group; an admin must start the bot privately for direct alerts.'
        elif 'thread' in lower or 'topic' in lower:
            text+=' · Check that the group’s General topic is open and available to the bot.'
        retry=self.parameters.get('retry_after')
        if type(retry) is int and retry>0:text+=f' · Retry after {retry} seconds.'
        return text


def _call(token,method,fields,photo=None):
    if photo is None:
        data=json.dumps(fields).encode();mime='application/json'
    else:
        boundary='zedair'+uuid.uuid4().hex
        data=b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k,v in fields.items())
        data+=f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="page.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()+photo+f'\r\n--{boundary}--\r\n'.encode()
        mime='multipart/form-data; boundary='+boundary
    req=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,data,{'Content-Type':mime})
    code=None
    try:
        with urllib.request.urlopen(req,timeout=12) as response:raw=response.read(16384)
    except urllib.error.HTTPError as exc:
        code=exc.code
        try:raw=exc.read(16384)
        finally:exc.close()
    try:
        result=json.loads(raw)
        if not isinstance(result,dict):raise ValueError()
    except (ValueError,UnicodeError):
        raise TelegramError({'description':'Telegram returned a non-JSON response'},token,code) from None
    if code or result.get('ok') is not True:raise TelegramError(result,token,code)
    return result


def _safe_error(exc):
    if isinstance(exc,TelegramError):return exc.explain()
    if isinstance(exc,(TimeoutError,urllib.error.URLError)):
        return type(exc).__name__+' · Could not reach Telegram. Check the laptop’s network, DNS and TLS connection.'
    return type(exc).__name__+' · Could not send the notification.'


def send(settings,message,photo=None,*,critical=False):
    token=str(settings.get('telegram_bot_token','')).strip()
    group=str(settings.get('telegram_chat_id','')).strip()
    keys=('telegram_chat_id','telegram_admin_id') if critical else ('telegram_chat_id',)
    ids=list(dict.fromkeys(str(settings.get(k,'')).strip() for k in keys if str(settings.get(k,'')).strip()))
    if not token or not ids:return {'ok':False,'errors':['Configure a bot token and '+('a group or admin ID' if critical else 'a group chat ID')]}
    if critical:photo=None
    errors=[];warnings=[];migrations={};delivered=set()
    for original_id in ids:
        cid=original_id
        fields={'chat_id':cid,'caption' if photo else 'text':message[:3000] if critical else sanitized(message)[:950 if photo else 3000]}
        if critical:fields['parse_mode']='HTML'
        method='sendPhoto' if photo else 'sendMessage'
        try:
            try:result=_call(token,method,fields,photo)
            except TelegramError as exc:
                migrated=exc.parameters.get('migrate_to_chat_id')
                # Only follow a server-reported migration of the configured group, once.
                if original_id==group and type(migrated) is int and -(2**52)<migrated<0 and str(migrated)!=cid:
                    cid=str(migrated);fields['chat_id']=cid
                    result=_call(token,method,fields,photo)
                    migrations[original_id]=cid
                    warnings.append('Telegram migrated the group; its new chat ID has been detected.')
                elif photo and exc.code==400 and any(x in exc.description.lower() for x in ('photo','image','media_empty')):
                    # A rejected image should not suppress the actual diagnostic text.
                    result=_call(token,'sendMessage',{'chat_id':cid,'text':sanitized(message)[:3000]})
                    warnings.append('Text delivered without screenshot: '+exc.explain())
                else:raise
        except Exception as exc:
            errors.append(f'{cid}: '+_safe_error(exc));continue
        delivered.add(cid)
        if critical and settings.get('telegram_pin_critical') and original_id==group:
            mid=result.get('result',{}).get('message_id')
            if mid:
                try:_call(token,'pinChatMessage',{'chat_id':cid,'message_id':mid,'disable_notification':False})
                except Exception as exc:warnings.append('Alert delivered; could not pin it: '+_safe_error(exc))
    return {'ok':not errors,'errors':errors,'warnings':warnings,'migrations':migrations,'delivered':sorted(delivered)}
