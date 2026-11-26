"""Actual systemd socket activation under reconnect polling, isolated unit names.
No packages are installed and no real Zedair service is touched.
"""
import json,os,re,socket,subprocess,sys,tempfile,threading,time,urllib.request
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def worker():
    import engine_update
    prefix='zedair-test-update-'+str(os.getpid());app=prefix+'.service';proxy=prefix+'-local.service';activation=prefix+'-local.socket'
    original=subprocess.run
    def system(*args,**kwargs):return original(['systemctl',*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,**kwargs)
    binary=next(p for p in ('/usr/lib/systemd/systemd-socket-proxyd','/lib/systemd/systemd-socket-proxyd') if Path(p).exists())
    backend=free_port();front=free_port();root=Path('/run/systemd/system');paths=[];stop=threading.Event();poller=None
    with tempfile.TemporaryDirectory() as tmp:
        status=Path(tmp)/'status.json'
        contents={app:f'[Service]\nExecStart={sys.executable} -m http.server {backend} --bind 127.0.0.1\nRestart=on-failure\n',
            proxy:f'[Unit]\nRequires={app}\nAfter={app}\n[Service]\nExecStart={binary} 127.0.0.1:{backend}\n',
            activation:f'[Socket]\nListenStream=127.0.0.1:{front}\n'}
        try:
            for name,value in contents.items():
                path=root/name
                with path.open('x') as f:f.write(value)
                paths.append(path)
            system('daemon-reload');system('start',app,activation)
            def poll():
                while not stop.wait(.02):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{front}',timeout=.25) as r:r.read(1)
                    except OSError:pass
            poller=threading.Thread(target=poll,daemon=True);poller.start()
            names={'zedair':app,'zedair-local.service':proxy,'zedair-local.socket':activation}
            install_checks=[]
            def run(args,**kwargs):
                if args[0]=='systemctl':return original([names.get(v,v) for v in args],**kwargs)
                if 'pip' in args:
                    assert system('show',app,'--property=ActiveState','--value').stdout.strip()=='inactive'
                    assert system('show',activation,'--property=ActiveState','--value').stdout.strip()=='inactive'
                    time.sleep(.3)
                    assert system('show',app,'--property=ActiveState','--value').stdout.strip()=='inactive'
                    install_checks.append(True)
                return subprocess.CompletedProcess(args,0,stdout='fixture version',stderr='')
            with patch.object(engine_update,'status_path',return_value=status),patch.object(engine_update,'UPDATE_LOCK',Path(tmp)/'lock'),patch.object(engine_update.importlib.metadata,'version',return_value='1.0'),patch.object(engine_update.subprocess,'run',side_effect=run):engine_update.update('yt-dlp')
            assert json.loads(status.read_text())['status']=='done',status.read_text()
            assert install_checks==[True]
            system('is-active','--quiet',app,activation)
            print('PASS: real systemd local socket is paused before app stop, reconnect polling cannot reactivate it during installation, and both return afterward.')
        finally:
            stop.set()
            if poller:poller.join(timeout=2)
            original(['systemctl','stop',activation,proxy,app],capture_output=True)
            for path in paths:path.unlink(missing_ok=True)
            original(['systemctl','daemon-reload'],capture_output=True)
            original(['systemctl','reset-failed',app,proxy,activation],capture_output=True)

if '--worker' in sys.argv:worker()
elif Path('/run/systemd/system').is_dir() and os.environ.get('GITHUB_ACTIONS')=='true':
    subprocess.run(['sudo',sys.executable,str(Path(__file__).resolve()),'--worker'],check=True,timeout=90)
else:
    if os.environ.get('ZEDAIR_REQUIRE_BROWSER')=='1':raise RuntimeError('CI must exercise the systemd updater integration')
    print('SKIP: actual systemd updater fixture runs in GitHub Actions; local workspace has no systemd manager.')
