#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then
  echo 'Run: sudo bash /opt/zedair/update.sh'
  exit 1
fi
systemctl stop zedair
trap 'systemctl start zedair' EXIT
/opt/zedair/venv/bin/python -m pip install --no-cache-dir --upgrade 'yt-dlp[default]' gallery-dl
/opt/zedair/venv/bin/python -m yt_dlp --version
/opt/zedair/venv/bin/python -m gallery_dl --version
echo 'Downloader engines updated. Queued jobs will recover when the service restarts.'
