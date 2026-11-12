"""Validation and command preparation for explicit, resumable profile tools."""
import importlib.util
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

MODES = ('posts', 'reels', 'everything')
BROWSERS = Path('/opt/zedair/browsers')


def browser_ready():
    spec=importlib.util.find_spec('playwright')
    if spec is None or not spec.origin or not BROWSERS.is_dir():return False
    try:
        manifest=json.loads((Path(spec.origin).parent/'driver/package/browsers.json').read_text())
        entry=next(b for b in manifest['browsers'] if b['name']=='chromium-headless-shell')
        root=BROWSERS/('chromium_headless_shell-'+entry['revision'])
        return any(p.is_file() for p in root.glob('**/chrome-headless-shell')) or any(p.is_file() for p in root.glob('**/headless_shell'))
    except (OSError,KeyError,ValueError,StopIteration):return False


def profile_target(platform, value):
    if platform not in ('instagram','facebook'):
        raise ValueError('Choose Instagram or Facebook')
    if not isinstance(value,str) or len(value)>500:
        raise ValueError('Enter a profile username or URL')
    value=value.strip().lstrip('@')
    if '://' in value:
        p=urlsplit(value)
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443):
            raise ValueError('Use an HTTPS profile URL')
        if p.hostname not in ((platform+'.com'), 'www.'+platform+'.com', 'm.'+platform+'.com'):
            raise ValueError('The URL does not match the selected platform')
        parts=p.path.strip('/').split('/')
        if platform=='facebook' and p.path=='/profile.php':
            value=parse_qs(p.query).get('id',[''])[0]
            if not value.isdigit():raise ValueError('Facebook profile.php needs a numeric id')
        else:
            if len(parts)>2 or (len(parts)==2 and parts[1] not in ('posts','reels','reels_tab','photos','videos')):
                raise ValueError('Enter a profile URL, not an individual post')
            value=parts[0]
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.]{0,79}',value) or value.endswith('.'):
        raise ValueError('Use a simple profile username or numeric Facebook ID')
    if value.lower() in {'p','reel','reels','reels_tab','tv','stories','explore','accounts','login','watch','share','photo','photos','photo.php','videos','groups','home.php','profile.php'}:
        raise ValueError('Enter a specific person/page profile')
    if platform=='instagram' and len(value)>30:raise ValueError('Instagram usernames have at most 30 characters')
    value=value.lower()
    return value, 'https://www.'+platform+'.com/'+value+'/'


def facebook_command(job,dest,cookie,settings,state):
    path=state/('profile-'+job['id']+'.json')
    path.write_text(json.dumps({'url':job['url'],'username':job['profile_username'],'mode':job['profile_mode'],
        'scroll_limit':job.get('scroll_limit',2000),'dest':str(dest),'cookie':str(cookie or ''),
        'media_kib':settings['media_kib'],'browsers':str(BROWSERS),'state':str(state),
        'browser_min_memory_mib':settings.get('browser_min_memory_mib',350)}))
    path.chmod(0o600)
    return [sys.executable,str(Path(__file__).with_name('facebook_profile.py')),str(path)]
