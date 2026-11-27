"""Keep gallery-dl first; use the authenticated browser on failed/empty output."""
import json
import sys
import os
import subprocess
from pathlib import Path
import download_http as http
from download_engine import execute, diagnostic, finish_media
from engine_settings import DEFAULTS
from publishing import MEDIA
from web_session import Session, browser_download_lease


def outputs(dest):
    return [p for p in Path(dest).iterdir() if p.is_file() and not p.is_symlink() and not p.name.startswith('.') and p.suffix.lower() in MEDIA and p.stat().st_size]


def run(spec):
    code,log=execute(spec['args'])
    # Profile checkpoint and intentionally empty image-only feeds have their own
    # protocol. An archived, already-published collection can also finish empty.
    if code in (75,79):return code
    if any(word in log.lower() for word in ('http error 429','too many requests','checkpoint_required','challenge_required','consent_required')):
        if code:return code
        raise ValueError('Instagram requires a cooldown or account verification; '+log[-1500:])
    if code==0 and (outputs(spec['dest']) or spec.get('published')):return 0
    reason='gallery-dl failed (exit '+str(code)+')' if code else 'gallery-dl returned success without producing media'
    if not spec.get('enabled',True):raise ValueError(reason+'; browser fallback is disabled')
    print('ZEDPROFILE '+reason+'. Trying authenticated Instagram browser fallback.',flush=True)
    settings={**DEFAULTS,**spec.get('settings',{})};spec={**spec,'settings':settings}
    session=Session(Path(spec['state']),spec['url'],spec.get('cookie',''),settings['auto_cookies'])
    # Never move a queue lease into a browser-only helper: it must cover transfers
    # too, including ffmpeg, and be shared with other browser download workers.
    with browser_download_lease(Path(spec['state'])):
        print('ZEDENGINE browser',flush=True)
        try:
            from instagram_browser import discover
            items=discover(spec,session.jar)
            client=http.opener(session.jar);dest=Path(spec['dest'])
            # Gallery may have succeeded on only part of a carousel. Keep its
            # outputs; publish from a separate verified batch only after ALL
            # selected browser items have downloaded successfully.
            stage=dest/'.instagram-browser'
            if stage.is_symlink():raise ValueError('Unsafe Instagram staging directory')
            stage.mkdir(mode=0o700,exist_ok=True)
            for item in items:
                print('ZEDPROFILE Instagram browser downloading '+item['key'],flush=True)
                candidate=http.inspect(client,item['url'],item.get('headers'),settings['http_timeout'])
                expected='video' if item['video'] else 'image'
                if candidate['kind']!=expected:raise ValueError('Instagram returned '+candidate['kind']+' instead of the requested '+expected)
                path=http.transfer(client,candidate,stage,item['key'],settings.get('media_kib',0),settings['http_timeout'],identity=item['key'])
                if item.get('audio_url'):
                    tracks=stage/'.tracks';tracks.mkdir(mode=0o700,exist_ok=True)
                    audio=http.inspect(client,item['audio_url'],item.get('headers'),settings['http_timeout'])
                    if audio['kind'] not in ('audio','video'):raise ValueError('Instagram audio track did not return media')
                    sound=http.transfer(client,audio,tracks,item['key']+'-audio',settings.get('media_kib',0),settings['http_timeout'],identity=item['key']+'-audio')
                    merged=tracks/(item['key']+'.mp4')
                    result=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-i',str(path),'-i',str(sound),
                        '-map','0:v:0','-map','1:a:0','-c','copy','-threads','1','-movflags','+faststart',str(merged)],capture_output=True,text=True,timeout=300)
                    if result.returncode:raise ValueError('Instagram video/audio merge failed: '+result.stderr[-1000:])
                    merged.replace(path);sound.unlink()
                if item['video']:finish_media(path,spec)
                if item.get('owner'):print('ZEDOWNER '+item['owner'],flush=True)
                print('ZEDITEM '+item['key'],flush=True)
            completed=outputs(stage)
            if not completed:raise ValueError('Instagram browser produced no verified media')
            # Only job-private staging is replaced. Existing published SSD files
            # are never removed. Avoid mixing partial gallery results with the
            # complete browser collection (duplicates or lower renditions).
            for p in outputs(dest):p.unlink()
            for p in completed:p.replace(dest/p.name);http.output_register(dest,p.name)
            if spec.get('cookie'):
                session.jar.save(spec['cookie'],ignore_discard=True,ignore_expires=False)
            return 0
        except Exception as exc:
            raise ValueError(reason+'; Instagram browser fallback: '+diagnostic(exc)) from exc
        finally:session.save()


if __name__=='__main__':
    try:raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
    except Exception as exc:print('ERROR '+diagnostic(exc),flush=True);raise SystemExit(1)
