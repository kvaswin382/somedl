"""Telegram notifications and browser snapshots."""
import json
import urllib.request
import urllib.error
import uuid

def send_message(bot_token, chat_ids, text):
    bot_token = str(bot_token or '').strip()
    if not bot_token:
        return {'ok': False, 'error': 'Bot token is required'}
    recipients = [str(c).strip() for c in chat_ids if str(c).strip()]
    if not recipients:
        return {'ok': False, 'error': 'Enter a private log group ID or admin user ID'}
    errors = []
    successes = []
    for cid in recipients:
        try:
            payload = json.dumps({'chat_id': cid, 'text': text}).encode('utf-8')
            req = urllib.request.Request(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                data=payload,
                headers={'Content-Type': 'application/json', 'User-Agent': 'Zedair/1.0'}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('ok'):
                    successes.append(cid)
                else:
                    errors.append(f'{cid}: {data.get("description", "Failed")}')
        except urllib.error.HTTPError as exc:
            try:
                err_data = json.loads(exc.read().decode('utf-8'))
                errors.append(f'{cid}: {err_data.get("description", exc.reason)}')
            except Exception:
                errors.append(f'{cid}: HTTP {exc.code} {exc.reason}')
        except Exception as exc:
            errors.append(f'{cid}: {exc}')
    if not successes and errors:
        return {'ok': False, 'error': '; '.join(errors)}
    return {'ok': True, 'successes': successes, 'errors': errors}


def send_snapshot(bot_token, chat_ids, photo_bytes, caption=''):
    bot_token = str(bot_token or '').strip()
    if not bot_token or not photo_bytes:
        return
    recipients = [str(c).strip() for c in chat_ids if str(c).strip()]
    if not recipients:
        return
    boundary = '----ZedairTelegram' + uuid.uuid4().hex
    for cid in recipients:
        try:
            parts = [
                f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{cid}\r\n'.encode('utf-8'),
                f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption[:950]}\r\n'.encode('utf-8'),
                f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="snapshot.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode('utf-8'),
                photo_bytes,
                f'\r\n--{boundary}--\r\n'.encode('utf-8')
            ]
            req = urllib.request.Request(
                f'https://api.telegram.org/bot{bot_token}/sendPhoto',
                data=b''.join(parts),
                headers={'Content-Type': f'multipart/form-data; boundary={boundary}', 'User-Agent': 'Zedair/1.0'}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                pass
            print(f'ZEDPROFILE Telegram snapshot sent to {cid}', flush=True)
        except urllib.error.HTTPError as exc:
            try:
                err_data = json.loads(exc.read().decode('utf-8'))
                print(f'Warning: Telegram snapshot to {cid} failed: {err_data.get("description", exc.reason)}', flush=True)
            except Exception:
                print(f'Warning: Telegram snapshot to {cid} failed: HTTP {exc.code} {exc.reason}', flush=True)
        except Exception as exc:
            print(f'Warning: Telegram snapshot to {cid} failed: {exc}', flush=True)
