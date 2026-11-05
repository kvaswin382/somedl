# Zedair Download Desk

A local web download manager built for your Ubuntu 24.04 x86_64 Zedair, 2 GB RAM, 32 GB eMMC and existing 512 GB exFAT USB SSD.

**This is an installable application, not a cloud-hosted downloader. It has not been installed on your laptop yet.** You run the installer on the laptop, then use the web page from any device on the same network.

## What it does

- First-run superuser creation, protected by a setup key printed on the laptop.
- Persistent login cookies lasting **35 days**. Logging out revokes that browser's session; changing the password revokes all sessions.
- YouTube and other yt-dlp-compatible video websites, including supported adult video extractors. Paste direct links; there is no site discovery or search directory.
- Instagram single posts, photos, full carousels and reels through gallery-dl. Highest available quality is requested; source sites can withhold original resolution, especially without fresh cookies.
- Threads **experimental public single-post support**: extracts the exact post's exposed photo/video variants and chooses the largest available dimensions. It does not substitute profile pictures or thumbnails. Login walls, changed page formats, private posts, whole profiles, replies and text-only posts are not supported reliably.
- YouTube quality ceilings, original-codec audio extraction, optional playlists (100 items by default) and available English/Malayalam subtitles.
- Magnet links and `.torrent` uploads, file selection, pause/resume, verification, upload/download limits and adjustable seeding ratio.
- Persistent background media queue, pause/resume, cancel, retry, move-to-front, history and recent engine output.
- Browse/create subfolders **inside `Zedair` only**. Each website job gets a unique resumable subfolder. Torrents use the selected folder and the filenames in the torrent.
- Browse saved files and download them from the SSD to your phone or computer. No bulk ZIP generation or media streaming server is included.
- Downloads and partial media files go to the SSD. Drive UUID, mount presence, write access and free space are checked before media work and during transfers.
- Plain HTML/CSS/JavaScript frontend with a small Python standard-library backend. No Docker, Node web server, Redis, Celery or heavyweight browser automation runs on the laptop.

## 1. Copy the package to your laptop

Download `Zedair-Download-Desk.zip` to your Windows computer. Both devices should be on the same LAN.

On the Ubuntu laptop terminal/SSH connection, find your username and IP:

```bash
whoami
hostname -I
```

On Windows, open PowerShell. Replace `YOUR_USERNAME` and `LAPTOP_IP` below with those results:

```powershell
scp "$env:USERPROFILE\Downloads\Zedair-Download-Desk.zip" YOUR_USERNAME@LAPTOP_IP:~/
```

If you saved the ZIP somewhere else, replace the source path. WinSCP using SFTP is an alternative: upload the ZIP into your Ubuntu home directory.

## 2. Install on Ubuntu

Connect the SSD. The supplied `lsblk` output showed `/dev/sda1`, 476.9 GiB, exFAT, unmounted. The installer detects the current device and asks you to select it; it does not assume the device letter stays `sda`.

```bash
sudo apt update
sudo apt install -y unzip
unzip Zedair-Download-Desk.zip
cd Zedair-Download-Desk
sudo bash install.sh
```

1. Select the exFAT SSD from the numbered list. Confirm its size and device name.
2. Type `INSTALL` after reviewing the destination. **No formatting or repartitioning is performed.** Existing files outside `Zedair` are kept intact.
3. Wait for the software downloads and installation. APT and PyPI need working internet, as does GitHub for Deno.
4. At completion, note the printed LAN URL and the **first-run setup key**.
5. From your phone or Windows browser on the same LAN, open `http://LAPTOP_IP:8787`.
6. Enter the setup key, choose a username and a password of at least 12 characters, and create your superuser.

The installer:

- Mounts the SSD by UUID at `/srv/zedair-ssd`, keeping exFAT for Windows compatibility.
- Backs up `/etc/fstab` before adding its managed entry. `nofail` allows Ubuntu to boot when the drive is unplugged.
- Creates `/srv/zedair-ssd/Zedair` plus `Videos`, `Instagram`, `Threads` and `Torrents` subfolders. It does not relocate existing data.
- Uses a dedicated `zedair` service account. exFAT permission masks allow this account to write; other Linux users can use the webpage or `sudo`. Windows can still read the drive normally.
- Installs the application in `/opt/zedair`; private account/queue/cookie data live in `/var/lib/zedair` on the eMMC.
- Enables `zedair.service` and a separate `zedair-transmission.service` at startup.
- Keeps the torrent control interface on **127.0.0.1:9092** with separate generated credentials.
- Does not configure router port forwarding, change UFW rules, or change laptop sleep settings.

If setup stops after a dependency download fails, fix the shown problem and rerun the same installer. It retains its saved mount/configuration. If it reports an existing unrelated mount/fstab entry, do not remove that entry blindly; share the error for help.

Recover the setup key, if needed:

```bash
sudo cat /etc/zedair/setup-key
```

The key only works before the first administrator is created. It is not a password-reset backdoor.

## 3. First download

1. Click **Add downloads**.
2. Paste one URL per line. For Instagram, use a direct post or reel link; a carousel downloads all of its exposed items.
3. Click **Browse** to select a folder within `Zedair`, or create a subfolder. You may also enter a nested path such as `Videos/Travel`.
4. Keep **Best available** for maximum quality. Resolution choices are ceilings, not guarantees. Instagram and Threads use their own best-available media selection.
5. Optionally name the job folder. Its unique ID prevents unrelated downloads from overwriting one another.
6. Click **Add to queue**. You can close the browser; the laptop continues working.

Queued duplicates are skipped. A previously completed URL can be downloaded again into a new folder. Pause terminates the engine cleanly; resuming reuses its folder and partial files where the source protocol supports it. Some websites restart a partial stream. Threads restarts the current partial file on retry because CDN links can change, but retains already completed items.

Retries wait 1, 2, 4… minutes (maximum 30-minute delay). The default is one initial attempt plus three automatic retries. A login-required/unsupported link eventually moves to **Attention** with the actual engine output. After fixing cookies or updating engines, click retry.

Progress for yt-dlp represents the current file/stream, not an entire playlist. Audio/video merging may continue after a stream reaches 100%. Instagram galleries show engine activity and completed file paths; a trustworthy overall percentage/speed/ETA is not available for every gallery.

## 4. Import website cookies when needed

Your laptop is headless, so it cannot read the browser login on your Windows computer automatically.

1. Log into the website in your own computer browser.
2. Export **only that website's cookies** to Netscape/Mozilla `cookies.txt` format using a trusted local exporter or browser tooling. The file begins with `# Netscape HTTP Cookie File`.
3. In Zedair, open **Settings → Website cookies**, then **Import** beside Instagram, YouTube, Threads, or Other.
4. Choose the exported `.txt` file. New jobs/attempts use it through the selected cookie profile.
5. Delete the exported copy from shared folders when you no longer need it.

Cookies are equivalent to an account session. Do not send them in chat or upload them to a third-party downloader. The app stores them privately on the eMMC, outside the removable SSD, and provides replacement/deletion controls. Cookie values are not returned in its API/UI.

Auto-selection uses Instagram cookies for Instagram, Threads cookies for Threads, YouTube cookies for YouTube/youtu.be, and Other for remaining websites. Cookie-domain rules still apply. Website cookies can expire or be rejected long before the desk's independent 35-day login expires.

Instagram may offer lower-resolution images even with a valid session. The engine records warnings if it knows the original dimensions were larger. No software can promise the original upload when the platform does not provide it.

## 5. Torrents

- Add a v1/hybrid magnet link or upload a `.torrent` file, choose a destination, then submit.
- **Add paused** is useful for selecting files in `.torrent` files before downloading.
- Magnet links need peers to fetch metadata. Start briefly, pause, then open the **Files** button to select files. A little payload may download before you pause. A magnet containing only a v2 `btmh` identifier is not accepted by this UI; use its `.torrent` file.
- File selection applies to future downloading. Already downloaded data is kept. Adjacent files can share torrent pieces.
- Removing a torrent keeps its files. There is no destructive “delete data” action in this version.
- Uploading to peers happens during download. The default seeding ratio is **0**, stopping seeding once complete. Set a ratio such as **1** in Settings when you want to seed.
- Default upload cap: **256 KiB/s**. Media and torrent download caps start unlimited. Adjust them for your wired connection.
- The daemon handles tracker/peer reconnects. A disk error or missing files may need a manual **Verify existing data** and resume after correcting the problem.

No web-server port should be forwarded on your router. Incoming BitTorrent connectivity is optional: downloads can work through outbound peers, but some swarms will be slower without reachable peer ports.

## 6. Using the SSD in Windows

Do not pull out the USB cable while downloads are running. exFAT is compatible with Windows but is not a journaling filesystem.

1. In Settings, click **Prepare to disconnect**. This holds the media queue and pauses active torrents.
2. On Ubuntu, stop both services and unmount:

```bash
sudo systemctl stop zedair zedair-transmission
sudo umount /srv/zedair-ssd
```

If unmount reports “busy”, keep the SSD connected and close any terminal/file process using it. Do not use forced/lazy unmount as the normal removal method.

3. Once `umount` succeeds, unplug the SSD and use it in Windows.
4. Safely eject it in Windows, reconnect it to Ubuntu, and run:

```bash
sudo mount /srv/zedair-ssd
sudo systemctl start zedair-transmission zedair
```

If `mount` says it is already mounted, continue with the service command. Open the desk and click **Resume queue**. Queues and cookies remain on the laptop. Torrents active at an unexpected disconnect may need manual resume/verification after reconnecting; the UI must be able to contact the daemon again.

The unmounted mount-point directory is root-owned and non-writable by the service account. This provides a second layer of protection against downloads falling through to the eMMC when the SSD disappears. Free-space protection has a monitoring delay and cannot reserve bytes against unrelated simultaneous writes; keep a generous reserve (5 GiB by default).

## 7. Keep the laptop awake with its lid closed

If it currently sleeps when you close the lid, create a logind drop-in:

```bash
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/zedair.conf >/dev/null <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
EOF
sudo reboot
```

Allow ventilation and leave the charger connected. You do not need a desktop environment. The app does not change your existing zram/swap settings.

## 8. LAN access and login security

The default page uses HTTP on your trusted home LAN. The password/session is not encrypted in transit over plain HTTP. Do not expose port 8787 to the internet, guest Wi-Fi, or an untrusted LAN. HTTP is used for simple first-time local setup; optional HTTPS is supported below.

If UFW is active and blocks the webpage, first find your actual LAN subnet using `ip -4 route`. For an example subnet of **192.168.1.0/24** (replace it with yours):

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8787 proto tcp comment 'Zedair home LAN'
```

Do not enable UFW blindly over SSH; retain the SSH rule. Do not add a global allow rule for 8787. The installer makes no firewall changes because your LAN subnet and existing SSH rules are unknown.

Reserve the laptop's IP address in your router's DHCP settings so its URL stays the same. Wired and Wi-Fi clients must be on the same LAN; guest-network isolation may block access.

### Optional HTTPS

The server can use a certificate and key already trusted by your devices. Obtain a trusted LAN certificate from your own local CA, with the laptop's LAN hostname/IP in its SAN. Put the files in `/etc/zedair`, readable by the `zedair` group and not by other users. Add these fields to `/etc/zedair/config.json` with `sudoedit`:

```json
"tls_cert": "/etc/zedair/lan-cert.pem",
"tls_key": "/etc/zedair/lan-key.pem"
```

Keep the rest of the JSON valid. Restart `zedair`, then use `https://LAPTOP_IP:8787`. The app sets the session cookie's `Secure` flag when its built-in TLS is enabled. Trust the issuing CA on each client rather than bypassing browser certificate warnings. TLS is optional and was not provisioned/tested by this package.

## 9. Maintenance and troubleshooting

Website extractors change often. To update yt-dlp and gallery-dl:

```bash
sudo bash /opt/zedair/update.sh
```

This briefly stops the web app, updates the two engines from PyPI and restarts it. Queued jobs recover. The original package pins the engine versions tested on 3 November 2026; the update command intentionally installs newer stable releases. Transmission/FFmpeg receive Ubuntu package updates. If yt-dlp later requires a newer Deno, update that runtime from its official release and verify it on the laptop.

Check services:

```bash
systemctl status zedair zedair-transmission --no-pager
sudo journalctl -u zedair -n 80 --no-pager
sudo journalctl -u zedair-transmission -n 80 --no-pager
findmnt /srv/zedair-ssd
sudo -u zedair test -w /srv/zedair-ssd/Zedair && echo 'SSD folder writable'
```

- **Webpage unreachable:** verify the IP/port, service status, LAN isolation and UFW rule.
- **SSD offline:** check cable/power, mount UUID and `findmnt`. Reconnect using the steps above. Do not disable `require_mount` to suppress the error.
- **Storage reserve reached:** free space outside the app or lower the reserve carefully; then resume. File browsing and copying out remain available below the reserve. Use Windows/SSH to delete files if necessary; this app does not delete your downloaded data.
- **YouTube failure:** update engines, ensure `/opt/zedair/bin/deno --version` works, check job output, and add fresh YouTube cookies only if needed. Some YouTube challenges require additional upstream support.
- **Instagram login/rate limit:** refresh cookies and retry after a delay. A new cookie export does not guarantee access. Single-post/reel links are supported; profile-wide crawls are deliberately excluded.
- **Threads no media:** only media embedded in the exact public post is handled. Do not interpret successful queue submission as proof that a particular Threads URL will download. Preserve the error and post URL for troubleshooting; never share the cookie file.
- **Torrents offline after reconnect:** start `zedair-transmission` after the SSD is mounted. Verify files if Windows moved or renamed them.
- **A job exceeds RAM limits:** it can fail/retry. Use a lower quality or smaller playlist. The service limit is a safety ceiling, not a promise of zero swap or a measured RAM footprint on your Atom.
- **Pause/resume:** preservation of partial data depends on the source. Cancellation/history clearing never deletes downloaded files.
- **Mobile:** Settings includes theme and sign-out controls as well as transfer settings.

App state is small and bounded to 1,000 media jobs with limited log tails. Clear finished history periodically. Transmission retains torrent metadata until removed; large numbers of torrents still consume resources. Keep a few active items on this machine. Downloader caches and OS package/log use are separate from your media storage; monitor `df -h /` occasionally.

### Forgotten administrator password

Use your existing Ubuntu `sudo` account. Stop the web app, delete only the administrator/session records, then start it and use the original setup key to create the administrator again. Downloads/history remain. This requires local sudo access; there is no unauthenticated web reset.

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

### Stop or disable

```bash
sudo systemctl disable --now zedair zedair-transmission
```

This leaves software, configuration, queue data and SSD files intact. Removing mount entries or user accounts is a separate manual task.

## Verification and scope

See `TEST-REPORT.md` for the checks run on the delivered package. The actual laptop, its physical SSD, Instagram/YouTube account sessions, real torrent swarms and every supported video site cannot be validated from here. Systemd mounting/installation needs its first real run on your Ubuntu host.

The package ships source and tests. No preview account, sample queue, browser screenshot, cookies, virtual environment, private key or populated database is included.

## Upstream projects

- yt-dlp: https://github.com/yt-dlp/yt-dlp
- yt-dlp supported extractors: https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md
- YouTube JavaScript runtime requirements: https://github.com/yt-dlp/yt-dlp/wiki/EJS
- gallery-dl: https://github.com/mikf/gallery-dl
- gallery-dl Instagram configuration: https://gdl-org.github.io/docs/configuration.html#extractor-instagram-videos
- Threads upstream support request: https://github.com/mikf/gallery-dl/issues/4281
- Transmission: https://github.com/transmission/transmission
- Transmission legacy RPC (Ubuntu 24.04): https://github.com/transmission/transmission/blob/4.0.6/docs/rpc-spec.md
- Deno: https://github.com/denoland/deno

The custom desk source is supplied under the included MIT license. Third-party engines are downloaded during installation and retain their respective upstream licenses.
