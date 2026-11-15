"""One-time import of torrents left behind by the old Transmission engine.

Reads /var/lib/zedair/transmission (never modifies or deletes it), adds each
torrent to qBittorrent with its original folder, file selection and
paused/active state, then lets qBittorrent verify the data already on the SSD.
A marker file makes this run once; leftovers can be deleted by hand afterwards.
"""
import json
import time
from pathlib import Path

from torrent_engine import bdecode

MAX_TORRENT = 8*1024*1024


def _resume(base, stem):
    path = base/'resume'/(stem+'.resume')
    try:
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 4*1024*1024:
            value, _ = bdecode(path.read_bytes())
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def import_transmission(bt, state, root, log=print):
    """Return a summary dict, or None when there is nothing to do or it already ran."""
    state, root = Path(state), Path(root).resolve()
    base = state/'transmission'
    marker = state/'qbittorrent'/'transmission-import.json'
    if marker.exists() or not (base/'torrents').is_dir():
        return None
    summary = {'added': 0, 'already_present': 0, 'skipped': [], 'finished': int(time.time())}
    for item in sorted((base/'torrents').iterdir()):
        if item.suffix not in ('.torrent', '.magnet') or item.is_symlink() or not item.is_file():
            continue
        resume = _resume(base, item.stem)
        destination = resume.get(b'destination', b'').decode('utf-8', 'replace')
        try:
            if not destination or not Path(destination).resolve().is_relative_to(root):
                raise ValueError('its folder is outside the Zedair folder or unknown')
            paused = bool(resume.get(b'paused', 0))
            skipped = [i for i, flag in enumerate(resume.get(b'dnd', [])) if flag]
            count = len(resume.get(b'dnd', []))
            if item.suffix == '.torrent':
                data = item.read_bytes()
                if len(data) > MAX_TORRENT:
                    raise ValueError('torrent file is too large')
                ident = bt.add(destination, metainfo=data, paused=True)
                if skipped and count:
                    bt.set_files(ident, [i for i in range(count) if i not in skipped], count)
                bt.recheck(ident)
                if not paused:
                    bt.start([ident])
            else:
                magnet = item.read_text(errors='replace').strip()
                if not magnet.startswith('magnet:?'):
                    raise ValueError('not a magnet link')
                bt.add(destination, magnet=magnet, paused=paused)
            summary['added'] += 1
        except Exception as exc:  # one bad file must not block the rest
            status = getattr(exc, 'status', None)  # engine errors carry an HTTP-style status
            if status == 409:
                summary['already_present'] += 1
            elif status == 503:
                raise  # engine offline: try again later, no marker written
            else:
                summary['skipped'].append([item.name, str(exc)])
    marker.write_text(json.dumps(summary))
    marker.chmod(0o600)
    log(f"Transmission import: {summary['added']} added, {summary['already_present']} already present, {len(summary['skipped'])} skipped")
    return summary
