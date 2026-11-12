"""Mirror install-browser.sh's per-executable Ubuntu sandbox permission in CI."""
import os,shutil,subprocess,sys
from pathlib import Path
if os.geteuid()!=0:raise SystemExit('Run this CI setup as root')
restriction=Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
if restriction.exists() and restriction.read_text().strip()=='1':
    root=Path(sys.argv[1]).resolve()
    binaries=sorted(p.resolve() for p in root.rglob('chrome-headless-shell') if p.is_file() and p.resolve().is_relative_to(root))
    if not binaries:raise SystemExit('Matching Chromium executable not found')
    parser=shutil.which('apparmor_parser')
    if not parser:raise SystemExit('AppArmor parser missing')
    profile=Path('/etc/apparmor.d/zedair-ci-browser')
    profile.write_text('\n'.join('profile zedair-ci-browser-'+str(i)+' "'+str(p)+'" flags=(unconfined) {\n  userns,\n}\n' for i,p in enumerate(binaries)))
    subprocess.run([parser,'-r',str(profile)],check=True)
    print('Granted sandbox namespace permission to CI Chromium only; global restriction retained.')
