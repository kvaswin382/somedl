# Maintenance and recovery

[← README](../README.md) · [Setup](setup.md) · [Usage](usage.md) · [Maintenance](maintenance.md)

### Roll back an app upgrade

Use the exact backup path printed by the updater:

```bash
cd ~/Zedair-Download-Desk
sudo ls /var/backups/zedair
sudo bash upgrade.sh --rollback /var/backups/zedair/REPLACE_WITH_TIMESTAMP
```

Rollback first saves a fresh backup, then restores the selected app, cookies, configuration and queue database. **Queue/history/settings changes since that backup are rolled back too.** SSD files stay as they are, so a restored queue can require verification. The updater does not back up the SSD payload or Python virtual environment. Do not use it to undo separate engine updates.

Backups contain private account sessions. Keep them private and keep at least the most recent known-good backup. They use internal storage; check `df -h /` and `sudo du -sh /var/backups/zedair` periodically. Delete older backups only after checking their exact names and confirming the current app works.

## Take the SSD to Windows safely

1. In **Settings → Storage**, click **Prepare to disconnect**.
2. On Ubuntu, run:

```bash
sudo systemctl stop zedair zedair-transmission
sudo umount /srv/zedair-ssd
```

3. Unplug only after unmount succeeds. If it says busy, close processes using the SSD; do not force-unmount it as a routine workaround.
4. Use the drive in Windows, then safely eject it there.
5. Reconnect to Ubuntu:

```bash
sudo mount /srv/zedair-ssd
sudo systemctl start zedair-transmission zedair
```

6. Open the webpage and **Resume queue**. If `mount` says already mounted, continue with service startup. Verify torrents if Windows moved their files.

The app checks the SSD UUID/mount before writing and during transfers. The unmounted mount-point directory is not writable by the service account, preventing fallback downloads onto the eMMC. Keep the reserve enabled: free-space monitoring has a delay and cannot reserve bytes against other programs.

## Update download engines separately

When websites change, update yt-dlp and gallery-dl without replacing the application:

```bash
sudo bash /opt/zedair/update.sh
```

This briefly stops the web service, installs current stable engine releases from PyPI, then restarts. It does not pull your GitHub app source. Initial installation uses `requirements.txt`; app upgrades retain the installed engine versions. Ubuntu updates maintain FFmpeg/Transmission. If YouTube later requires a newer Deno, update Deno from its official upstream release and verify `/opt/zedair/bin/deno --version`.

## Troubleshooting

Start with:

```bash
systemctl status zedair zedair-transmission --no-pager
sudo journalctl -u zedair -n 80 --no-pager
sudo journalctl -u zedair-transmission -n 80 --no-pager
findmnt /srv/zedair-ssd
df -h /
df -h /srv/zedair-ssd
```

| Problem | What to check |
| --- | --- |
| Old webpage after a pull | Run `sudo bash upgrade.sh` from the updated checkout, then Ctrl+F5. Pulling source alone does not deploy it. |
| Webpage cannot connect | Laptop IP, port 8787, service status, firewall and guest-network isolation. |
| SSD offline | USB power/cable, mount UUID and `findmnt`. Reconnect with the Windows/SSD steps. Never disable `require_mount` to hide the error. |
| Free-space reserve reached | Delete unwanted files through SSD files, or free space in Windows. Browsing/deletion work below the reserve. |
| Instagram profile shows a count but no files | Upgrade to v2.1.2, then retry the same profile job; see [collection recovery](#instagram-collection-recovery). Existing working cookies need no replacement for this bug. |
| Website DNS lookup fails | Run the [hostname checks](#website-dns-checks) on the laptop; other sites working does not establish that this domain resolves. |
| Browser engine unavailable | Rerun `install-browser.sh`, check its sandbox test, restart the app. |
| Instagram pagination loop | Progress is retained; the server repeated a completed page/cursor. Review Details/update engines before retrying. |
| Instagram fails | Read Details; renew cookies, finish a browser challenge, wait for a rate limit or update engines as appropriate. |
| Facebook profile stops | Inspect the experimental collector log; check session, scroll limit, layout changes and browser installation. A finished job is not proof of a complete archive. |
| MP4 container conversion failed | The source codec cannot be copied into MP4. Select Keep source container; no lower-quality version is silently substituted. |
| Reddit video fails | Use a direct Reddit post/video link, update engines, and import Reddit cookies if that content needs login. |
| X video fails | Confirm it is a video status URL; check your session and update engines. Private/deleted/restricted posts can remain unavailable. |
| YouTube fails | Update engines, check Deno, inspect Details, and add YouTube cookies when required. |
| Threads finds no media | Embedded exact-post media is unavailable or changed. Experimental extraction cannot handle every login wall/page format. |
| Torrent offline after reconnect | Mount the SSD, then `sudo systemctl start zedair-transmission`. |
| File deletion blocked | Remove the owning torrent first, cancel the unfinished legacy job, or refresh if the file changed. |
| Speed test fails | Check laptop internet/DNS and endpoint reachability. Proxy/firewall restrictions or endpoint errors can prevent a test. No result is fabricated. |
| RAM limit reached | Pause torrents during Facebook collection; reduce playlist size. One media worker and one torrent still share the 2 GB RAM. |

### Storage and partial-file cleanup

Media lives on the SSD. Account/queue state is on eMMC. History is limited to 1,000 jobs; clear finished history periodically. Keep only a few torrents loaded. Logs are bounded per job. Profile manifests/archives grow with the collection on the SSD; partials, engine caches and backups can accumulate.

New jobs use `.incomplete/JOB_ID` on the SSD. Completed-job metadata is cleaned after publication. Cancelled/failed partials are retained. To reclaim them manually, stop **zedair**, inspect the exact job ID/path, and remove only that job’s working folder when you no longer need to resume it. Never delete all working folders while jobs are active. Old releases placed partials in visible per-job folders; finish or cancel those jobs before managing their files.

### Forgotten administrator password

From your Ubuntu sudo account, reset only the app administrator and sessions. Downloads/history remain:

```bash
sudo systemctl stop zedair
sudo -u zedair /opt/zedair/venv/bin/python - <<'PY'
import sqlite3
with sqlite3.connect('/var/lib/zedair/desk.sqlite3') as db:
    db.execute('DELETE FROM sessions')
    db.execute('DELETE FROM admin')
PY
sudo systemctl start zedair
sudo cat /etc/zedair/setup-key
```

Open the webpage and create the administrator again with that key. There is no unauthenticated web password reset.

### Stop automatic startup

```bash
sudo systemctl disable --now zedair zedair-transmission
# If you enabled zed.local, also stop its socket so it cannot start the app:
sudo systemctl disable --now zedair-local.socket
sudo systemctl stop zedair-local.service
```

This keeps files and configuration. To re-enable: `sudo systemctl enable --now zedair-transmission zedair` with the SSD connected.

## Where things live

| Path | Purpose |
| --- | --- |
| Your Git checkout, e.g. `~/Zedair-Download-Desk` | Source you pull from GitHub |
| `/opt/zedair` | Installed application, Python environment and Deno |
| `/opt/zedair/browsers` | Optional playback-detection / Facebook Chromium installation |
| `/etc/systemd/system/zedair-local.socket` and `.service` | Optional port-80 local address proxy |
| `/etc/zedair` | Private service configuration and setup key |
| `/var/lib/zedair` | Account, login sessions, queue, preferences, cookies and torrent state |
| `/srv/zedair-ssd/Zedair` | Downloads on the USB SSD |
| `/var/backups/zedair` | Private backups made by the app upgrader |

The implementation uses Python’s standard library for the server, plain HTML/CSS/JS, yt-dlp, gallery-dl, FFmpeg, Deno and a private Transmission daemon. No Docker, Redis or Node server is required. The optional Facebook profile tool launches one sandboxed Playwright Chromium browser only during collection, then closes it before video downloads. Direct Facebook videos do not need that browser. The service has CPU/memory limits; these are safeguards, not a measured performance guarantee on your hardware.

## Instagram collection recovery

Retry the existing profile job after upgrading to preserve its media-ID archive and partial files. Old v1.3 false-completion counters are repaired automatically. Do not delete working cookies or SSD folders for that historical counter bug. Inspect Details for actual authentication, verification, rate-limit or pagination errors before retrying.

## Website DNS checks

Replace `example.com` with the hostname shown in the failed job. Run these on the laptop:

```bash
getent ahosts example.com
resolvectl query example.com
resolvectl status
```

Other websites working does not establish that this hostname resolves. Check the laptop/router DNS configuration and whether the source domain still exists; retry after resolving the reported issue.
