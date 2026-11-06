#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo 'Run: sudo bash /opt/zedair/install-facebook.sh'; exit 1; fi
if [ ! -x /opt/zedair/venv/bin/python ]; then echo 'Install Zedair first.'; exit 1; fi
python3 - <<'PY'
import shutil
if shutil.disk_usage('/opt/zedair').free < 1536*1024**2:
    raise SystemExit('Keep at least 1.5 GiB free on the internal drive before installing the optional browser.')
PY
/opt/zedair/venv/bin/python -m pip install --no-cache-dir 'playwright==1.64.1'
PLAYWRIGHT_BROWSERS_PATH=/opt/zedair/browsers /opt/zedair/venv/bin/python -m playwright install --with-deps --only-shell chromium
chmod -R a+rX /opt/zedair/browsers
printf '%s\n' 'Optional Facebook collector installed. No login or collection was started.' 'Restart: sudo systemctl restart zedair' 'Import Facebook cookies in Settings → Platforms, then use Tools.'
