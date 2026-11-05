# Zedair Download Desk — v1.2

A private web downloader for your Ubuntu 24.04 laptop. Open it from your phone, tablet or computer on the same home network. The laptop does the downloading and saves files on the USB SSD.

Built for your x86_64 Zedair with 2 GB RAM, 32 GB eMMC and an existing exFAT SSD. **Do not format the SSD.** Its existing files can stay where they are. This package needs to be installed or upgraded on your laptop; downloading this source does not change the running server.

## Already installed? Update from GitHub

First copy the contents of this updated package into your GitHub project, commit and push them. Include the new Python modules, `upgrade.py`, `upgrade.sh`, `static/`, README and tests. Keep `server.py` and `upgrade.sh` together at the repository root. Do not commit cookies, private configuration, databases or backups.

On the **Ubuntu laptop**, open a terminal or SSH session. Replace the first path with your actual Git checkout:

```bash
cd ~/Zedair-Download-Desk
git status
git pull --ff-only
sudo bash upgrade.sh
```

**Run the upgrade only after `git pull` succeeds.** `git pull` downloads source into your checkout; `upgrade.sh` installs it into `/opt/zedair`, where the running service reads it. Pulling alone does not update the app.

If you already pulled the latest code, just run:

```bash
sudo bash upgrade.sh
```

The upgrade:

1. Checks the new Python files and existing engine installation.
2. Briefly stops the web and torrent services.
3. Backs up the installed app, account, sessions, cookies, queue, settings and configuration under `/var/backups/zedair/TIMESTAMP`.
4. Copies the new application files to `/opt/zedair`. Existing database records are migrated at startup.
5. Restarts the services and checks the web app. On a detected failure, it restores the previous app and private state.

It **does not format, move or delete your SSD files**, change `/etc/fstab`, or reinstall Python engine versions. Existing login sessions remain valid. If the SSD is disconnected, the web interface can start while the torrent service waits for the drive.

For the new speed-test engine, run these **once after upgrading to v1.2** (the same steps apply to a first installation):

```bash
sudo bash /opt/zedair/install-speedtest.sh
sudo systemctl restart zedair
```

This downloads the official Ookla CLI from its publisher’s Packagecloud repository, verifies a pinned SHA-256 checksum and installs it locally. It does not run a test or accept agreements. In the webpage, open **Speed test**, review the first-use agreement and check it yourself before starting. Future app upgrades preserve this engine; you do not need to reinstall it each time. The rest of the downloader works without it.

After success:

```bash
systemctl status zedair zedair-transmission --no-pager
```

Refresh the page with **Ctrl+F5** on Windows. On your phone, reload the page. The footer should show **v1.2.0**. Existing jobs keep their old folders so they can resume; newly added jobs use platform folders. To use the new layout for a failed old job, cancel it and add the URL again. Existing files are not relocated automatically.

### First time using Git on this laptop

If the app is already installed but you have no checkout yet:

```bash
sudo apt update
sudo apt install -y git
cd ~
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git Zedair-Download-Desk
cd Zedair-Download-Desk
sudo bash upgrade.sh
```

Replace the repository URL with your own. For a private repository, use your normal GitHub SSH key or credential manager. Do not put tokens inside commands or URLs. If your code is inside an extra subfolder, `cd` into the folder containing `upgrade.sh` before running it.

If Git reports local changes or diverging branches, stop and review `git status`. Commit or deliberately preserve your changes before pulling. Do not use `git reset --hard` just to clear the error.

### Roll back an app upgrade

Use the exact backup path printed by the updater:

```bash
cd ~/Zedair-Download-Desk
sudo ls /var/backups/zedair
sudo bash upgrade.sh --rollback /var/backups/zedair/REPLACE_WITH_TIMESTAMP
```

Rollback first saves a fresh backup, then restores the selected app, cookies, configuration and queue database. **Queue/history/settings changes since that backup are rolled back too.** SSD files stay as they are, so a restored queue can require verification. The updater does not back up the SSD payload or Python virtual environment. Do not use it to undo separate engine updates.

Backups contain private account sessions. Keep them private and keep at least the most recent known-good backup. They use internal storage; check `df -h /` and `sudo du -sh /var/backups/zedair` periodically. Delete older backups only after checking their exact names and confirming the current app works.

## What is supported

| Source | Downloads | Default destination |
| --- | --- | --- |
| YouTube / youtu.be | Videos, audio-only, optional playlists and subtitles | `Zedair/YouTube` |
| Instagram | Single posts, all carousel items, photos, reels and videos | `Zedair/Instagram` |
| X.com / Twitter | Video posts and animated GIFs via a direct status URL | `Zedair/X` |
| Threads | Experimental photos/videos from an exact public post | `Zedair/Threads` |
| Other supported video sites | yt-dlp-supported URLs, including supported adult sites | `Zedair/website-domain` |
| BitTorrent | v1/hybrid magnets and `.torrent` files | `Zedair/Torrents` by default |

This is a downloader, not a website search directory. Availability depends on the source website and your session. DRM-protected media, Instagram profile-wide crawling and X photo/feed crawling are not included. Threads support is experimental: login walls and changes to embedded page data can prevent downloads. It will not substitute profile pictures for missing post media.

The interface includes a charcoal sidebar, light/dark themes, platform settings, a persistent background queue, image/video previews, file browsing/deletion and an **Ookla speed test run on the laptop**, with saved reports. Closing your browser does not stop work. There is one media job and one active torrent download at a time to keep resource use modest.

## First installation

Skip this section if Zedair is already installed; use the upgrade instructions above.

### 1. Get the source onto Ubuntu

Using GitHub:

```bash
sudo apt update
sudo apt install -y git
cd ~
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git Zedair-Download-Desk
cd Zedair-Download-Desk
```

Or download the ZIP on Windows, then copy it using PowerShell:

```powershell
scp "$env:USERPROFILE\Downloads\Zedair-Download-Desk.zip" YOUR_UBUNTU_USERNAME@LAPTOP_IP:~/
```

On Ubuntu:

```bash
sudo apt update
sudo apt install -y unzip
cd ~
unzip Zedair-Download-Desk.zip
cd Zedair-Download-Desk
```

Find your Ubuntu username with `whoami` and the laptop IP with `hostname -I`. WinSCP/SFTP is also suitable for copying the ZIP. Extract into a clean folder so old files do not get mixed into a new release.

### 2. Connect the SSD and install

```bash
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS
sudo bash install.sh
```

1. Select your roughly 476.9 GiB exFAT SSD from the numbered list. Device names can change; check its size and filesystem.
2. Review the selected disk, then type `INSTALL`.
3. Wait for Ubuntu packages, Python packages and the Deno runtime to install. Internet access to Ubuntu mirrors, PyPI and GitHub is needed. Keep at least 2 GiB free on the eMMC.
4. Note the printed LAN URL and setup key.
5. Open `http://LAPTOP_IP:8787` from another device on your home network.
6. Enter the setup key and create your superuser with a password of at least 12 characters.

The installer never formats or repartitions a drive. It mounts the SSD by UUID at `/srv/zedair-ssd` and uses its `Zedair` folder. Files elsewhere on the SSD are not exposed by the app. exFAT stays compatible with Windows.

If the selected SSD is already mounted or has an existing conflicting fstab entry, the installer stops for you to review it. Do not blindly remove mount entries. Fix dependency/network errors and rerun the installer if the initial installation stops.

For speed testing, also follow **Test the laptop’s internet speed → One-time engine installation** below. Other downloads do not require that optional engine.

### 3. Check the installation

```bash
systemctl status zedair zedair-transmission --no-pager
findmnt /srv/zedair-ssd
hostname -I
```

Both services normally show `active` with the SSD connected. Recover the first-run key with:

```bash
sudo cat /etc/zedair/setup-key
```

That key works only before an administrator exists. Browser logins last **35 days**. Sign out revokes that browser’s login; changing the administrator password signs out all devices. Platform cookies have separate expiry rules.

## Add downloads and choose folders

1. Open **Downloads → Add downloads**.
2. Paste one direct URL per line, up to 30. For X, use `https://x.com/USERNAME/status/POST_ID`; old `twitter.com` links also work. Instagram uses a `/p/`, `/reel/` or `/tv/` link.
3. Leave the destination empty for `Zedair`, or click **Browse** to choose/create a subfolder.
4. Keep **Use platform defaults**, or choose a video quality/audio preset. Quality options are ceilings, not promises that a source offers that resolution.
5. Optionally add a queue display label. This label does not rename the video file.
6. Leave **Website cookies** on automatic selection unless you deliberately need another profile.
7. Add to the queue. You may close the browser.

With destination `Travel`, a YouTube download lands in `Zedair/Travel/YouTube`. The platform folder is always appended. Change its name in **Settings → Platforms & cookies** if wanted. For unknown video sites, the hostname is used unless you set a shared Other websites folder.

### Filenames and duplicates

A video’s source title becomes the filename when available. Instagram uses the available caption; content without a title/caption uses its source filename or post identifier. Names are shortened and cleaned for Windows/exFAT compatibility.

Example: `My video.mp4`, then `My video (1).mp4`, then `My video (2).mp4`. Existing files are never intentionally replaced. Multiple carousel items with the same caption are numbered too. Torrent filenames come from the torrent and follow Transmission’s normal resume/verification behavior.

Partial media and metadata live in hidden `Zedair/.incomplete/JOB_ID` folders. Completed new jobs publish media directly into the platform folder; there are no visible random job subfolders. Internal metadata is not offered in the file browser. Cancelled/failed jobs retain partial data for recovery; see storage cleanup below.

Unfinished duplicate URLs are skipped. To deliberately download a finished URL again, add it as a new job.

### Queue controls and errors

- **Pause** stops the current engine and keeps partial files. **Resume/retry** reuses them where the source supports it.
- **Hold queue** pauses media work and active torrents. **Resume queue** releases the hold.
- **Cancel** retains files; **Clear finished history** removes completed/cancelled queue records only.
- **Move to front** changes the waiting order.
- **Details** shows the destination, current status and recent engine output.

Temporary failures use bounded retries: one initial attempt plus three retries by default, waiting 1, 2, 4… minutes, capped at 30 minutes. Recognized authentication, rate-limit, unsupported-media and access failures go directly to **Attention**. Fix the cause, then retry manually. A generic 403 does not prove the cookies are wrong.

Video progress usually measures the current stream, not a whole playlist. Merging can continue after 100%. Instagram shows engine activity, but not every gallery exposes a dependable percentage or ETA. Downloading the best quality does not involve video re-encoding; FFmpeg can merge separate audio/video streams.

## Settings and website login cookies

Open **Settings**. The submenu has **General & transfers**, **Platforms & cookies**, **Storage**, and **Account & appearance**.

General settings include media/torrent speed limits, torrent seeding ratio, free-space reserve and retry count. Defaults are a 5 GiB SSD reserve, unlimited download speed, 256 KiB/s torrent upload and seeding ratio 0. KiB/s means kibibytes/second; 1,024 KiB/s is about 8.4 Mbps. Threads currently has no speed cap. YouTube has its own playlist limit in its platform tab; the general playlist limit is the fallback for other video extractors.

Each platform tab has its folder, enable switch and applicable options. YouTube has quality, playlist and subtitle defaults. Instagram has session requirements, best-stream/pre-merged video selection, resolution warnings and request delay. X has quality and an advanced API selector; keep **GraphQL** unless troubleshooting an upstream compatibility issue. Disabled platforms reject new jobs; they do not cancel existing ones.

Defaults are copied into newly queued jobs. Cookie replacements and live engine options apply on the next attempt. Checking playlist/subtitle options in Add downloads also enables them when using platform defaults. To override platform defaults with those options off, select an explicit quality preset and leave the checkboxes unchecked.

### Instagram: paste your session from Firefox on Windows

1. On your Windows computer, open Instagram in Firefox and sign in normally.
2. Open the post in that browser. Finish any login challenge there first.
3. Press **Shift+F9** to open Firefox’s Storage Inspector. Alternatively press F12 and choose **Storage**.
4. Expand **Cookies**, then choose the `https://www.instagram.com` entry (the exact displayed hostname may vary).
5. Find the cookie named **sessionid** and copy its **Value**. Do not copy the entire table or cookie name.
6. In Zedair, open **Settings → Platforms & cookies → Instagram**.
7. Paste into **sessionid**. Optionally copy **csrftoken** and **ds_user_id** from the same browser into their matching fields.
8. Click **Save private session**. Return to **Downloads → Attention** and retry the job.

The default requires a usable stored Instagram session before it starts downloading. You can turn this off for public-post testing, but many posts still require authentication. A “Session stored” label only checks the cookie file and required fields; it does not prove Instagram currently accepts the login.

### X / Twitter cookies

Public videos may work without a session. If a video needs your signed-in session:

1. Sign into **x.com** in Firefox and open the status URL.
2. Use **Shift+F9 → Cookies → x.com**.
3. Copy **auth_token** and **ct0** values.
4. Open **Settings → Platforms & cookies → X / Twitter**, paste both values, then **Save private session**.
5. Retry the job. Both fields are required for the manual form.

Cookies do not guarantee access to protected, deleted, region-limited or otherwise unavailable content. The X tab downloads videos/GIFs, not whole profiles or photos.

### Import cookies.txt for any platform

You can use **Import cookies.txt** instead of the manual fields. YouTube, Threads and Other websites use this import method.

Export the relevant website’s cookies in **Netscape/Mozilla cookies.txt** format using a trusted local browser exporter/tool. The first line is `# Netscape HTTP Cookie File`. Do not paste JSON or a raw HTTP Cookie header. Select the matching platform tab, import the file, and retry the affected download. For YouTube, follow the upstream yt-dlp cookie guidance linked at the end.

Saving a manual session or importing a file replaces that platform’s previous cookie file. Expiry-zero browser session cookies are normalized. Unsupported platform-domain cookies are filtered from named profiles. **Remove session** deletes the private stored copy. It does not sign your browser out of the original website.

Cookies are account credentials: do not put them in GitHub, chat, screenshots or shared folders. They are stored under `/var/lib/zedair`, not on the removable SSD. Inputs are masked, values are not returned to the webpage, and recognized secret values are redacted from job logs. Only use this interface on a trusted LAN or configure HTTPS below. Plain HTTP does not encrypt passwords/cookies in transit.

### Instagram works once, then says the cookie expired

**v1.2 fixes a reproducible local bug.** Gallery-dl writes session cookies with expiry `0` after downloading. This means “session cookie, no fixed expiry,” but v1.1’s status reader interpreted it as January 1970. That could block the next job even though Instagram still accepted the session.

After upgrading, retry the affected job from **Attention** using the cookies already stored. No re-import is needed to correct expiry-zero files. An explicit positive timestamp in the past remains expired; the app does not invent a new expiry or extend a revoked login.

Each Instagram job now uses a private working copy. Gallery-dl applies cookies returned by Instagram during its normal requests. After a successful download, Zedair normalizes and atomically saves that updated cookie jar for the next job, including refreshed session/CSRF values when the website supplies them. A failed download does not overwrite the saved session. If you replace or remove cookies in Settings while a job runs, your action takes priority over that older job’s updates. Settings shows when the saved file was last updated.

This is automatic response-cookie refresh, not repeated password login or an artificial keep-alive timer. There is no official promise that a copied session will last a month, and no app can force Instagram to renew it. A browser login can remain active while a separate server request is challenged. We do not request your Instagram password or bypass verification. The expiry-zero failure was reproduced with the real gallery-dl cookie writer; live access with your account still needs checking on the laptop.

If a request still fails, open **Details**. Verification/challenge errors, rate limits, rejected authentication and transient network errors have distinct messages. Resolve verification on Instagram, wait after rate limits, and replace cookies only when necessary. Highest resolution means the best media the site provides to that session, not guaranteed original upload resolution.

## Preview, download and delete files

Open **SSD files** and enter a platform folder. Click an image/video filename marked **Preview** to open it inside the page. Videos have native playback, volume, seeking and fullscreen controls; supported audio files also have a player. Closing the preview stops playback. Click the separate **Download** button only when you want a copy on the current phone/computer. **New folder** creates a subfolder. Lists show up to 1,000 entries per folder.

Previews require your normal login and stream the original file from the SSD. They do not convert video or use FFmpeg in the background, which keeps CPU/RAM usage low on the laptop. Your browser still transfers media bytes to display/play them. MP4 with H.264/AAC and common JPEG/PNG/WebP images usually have the broadest browser support. MKV, HEVC, some audio formats and unfinished torrent files may not play in your browser even when the download is valid. Use **Download** and a compatible player in that case. SVG/HTML files are not rendered inline. Seeking uses HTTP byte ranges; a disconnected SSD or expired login can interrupt playback.

Click **Delete** beside an individual file, check its full path, then confirm. **This permanently deletes it from the SSD; there is no recycle bin.** There is no recursive delete-folder button. Files outside `Zedair`, hidden working folders and symbolic links are not accessible.

Deletion is refused if the file changed since listing, is still being published, belongs to an unfinished legacy job, or is managed by a torrent. For a torrent file, remove the torrent from **Torrents** first (which keeps its files), then delete the file in **SSD files**. The torrent engine must be reachable to verify this protection; if it is offline, reconnect/mount the SSD and start the torrent service first.

Deleting a file does not remove its old queue history entry. Clearing history does not delete files.

## Torrents

1. Choose **Torrents → Add torrent**.
2. Paste a v1/hybrid magnet or select a `.torrent` file.
3. Choose a folder. The default is `Torrents` under `Zedair`.
4. Use **Add paused** if you want to choose files before starting a `.torrent` download.

For a magnet, start it briefly to get metadata, pause it, then use its file-selection button. Some payload may arrive before pausing. Unchecked files stop future downloading; already downloaded pieces remain. Files can share torrent pieces.

**Remove torrent** keeps its downloaded data. **Verify existing data** checks existing pieces. The default stops seeding at completion (ratio 0); uploading can still happen during download. Change the ratio in General settings if you want continued seeding. Magnet-only v2 `btmh` links are not accepted by this UI; use a compatible `.torrent` file.

## Test the laptop’s internet speed

### One-time engine installation

After installing or upgrading Zedair, run on the **laptop**:

```bash
sudo bash /opt/zedair/install-speedtest.sh
sudo systemctl restart zedair
```

The installer targets this x86_64 laptop. It retrieves the official Ookla CLI 1.2.0 package, checks its SHA-256 checksum and puts the binary at `/opt/zedair/bin/speedtest`. It does not add an APT repository or replace an existing system speedtest package. The publisher’s manual is copied to `/opt/zedair/speedtest-docs/speedtest.5`. A checksum/network failure stops installation; do not bypass checksum validation. Internet access to Ookla’s Packagecloud download and its redirected file host is needed.

This is the **official Ookla CLI**, not the Python package called `speedtest-cli`. The app also recognizes an already installed official `/usr/bin/speedtest`. If the page says the engine is missing, complete the commands above and reload.

### Run a test

1. Open **Speed test** from any device on your LAN.
2. Before the first test, read the linked Ookla license, terms and privacy notice. The CLI’s license is for personal, non-commercial use. Check the agreement yourself if you accept it. The installer never accepts it for you.
3. For a clearer result, **Hold queue**, wait for active media/torrents to stop and avoid other heavy traffic on the network.
4. Click **Start test**. The laptop runs the actual internet test; your phone or computer only displays its progress. The nearby test server is selected automatically.
5. Read **Download** and **Upload** in Mbps, plus **Ping** and **Jitter** in milliseconds. Click **Cancel test** to stop early. Resume the queue when finished.

The large number shows the live rate during a transfer and the final download rate when finished. Live rates can fluctuate; the completed report uses Ookla’s final result. The app converts the engine’s bandwidth from bytes/second to decimal Mbps using `bytes_per_second × 8 ÷ 1,000,000` (25,000,000 bytes/s = 200 Mbps). It never caps or invents a result to match your plan. Only a valid final result with a successful process exit counts as completed.

The last **100 reports** remain across restarts and app upgrades. **View** opens report details, server/ISP, data transferred, duration and the official result link when available. **Export CSV** saves the report history to your current device. Failed/cancelled attempts are identified separately. Old v1.1 measurements are retained as **Legacy estimate · unreliable** and are not used as the latest successful result.

Only one test runs at a time, starts must be at least 30 seconds apart, and a test is stopped after 180 seconds. Ookla adapts transfer sizes: tests can use **hundreds of megabytes or more** of internet data. There is no longer a 41 MiB limit. Payload is not saved as downloaded files. Ookla/test servers receive the laptop connection’s public IP and test/network information under their terms; platform session cookies are not supplied. The app stores report metrics, not raw IP/MAC fields from engine output.

### Understand unexpected results

A 200 Mbps internet plan and an Ethernet link speed are different measurements. The test measures internet throughput, not the local Ethernet cable’s negotiated rate. Server choice, other traffic, ISP provisioning/bursting, CPU load, USB adapters and the laptop’s hardware can affect a result. The app’s service resource limits remain in effect; on the old Atom CPU these can reduce measured throughput. Pause transfers and compare several completed reports at different times. Do not compare Mbps directly to MB/s (divide Mbps by 8 for decimal MB/s).

The previous short HTTP test was not reliable enough; its payload/timing method has been replaced. We could not establish the exact reason for your old 350/300 Mbps readings without testing the physical laptop. If the new engine still reports unexpected values, inspect **View → Open Ookla result** and compare with your router/ISP information. No successful live speed measurement was performed on your laptop while preparing this release.

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

## Keep running with the lid closed

If Ubuntu sleeps when you close the lid:

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

Keep ventilation clear and the charger connected. A desktop environment is unnecessary. Existing zram/swap settings are preserved. Both services start automatically after boot when their dependencies are available.

## Network access and HTTPS

Reserve the laptop’s IP in your router’s DHCP settings so its URL stays stable. Phone/computer and laptop must be on the same LAN; guest Wi-Fi isolation can block access. Do not forward web port **8787** or torrent RPC port **9092** to the internet.

The default webpage uses HTTP. For a trusted home LAN this keeps setup simple; HTTP does not encrypt credentials. If you need encrypted access, supply a certificate/key trusted by your devices, with the laptop’s hostname/IP in its SAN. Store them in `/etc/zedair`, readable by the zedair group, and add `tls_cert` and `tls_key` to the existing JSON using `sudoedit /etc/zedair/config.json`:

```json
"tls_cert": "/etc/zedair/lan-cert.pem",
"tls_key": "/etc/zedair/lan-key.pem"
```

Preserve valid JSON, including commas. Restart `sudo systemctl restart zedair` and use `https://LAPTOP_IP:8787`. The app then sets its login cookie’s Secure flag. Trust your local CA on each device instead of bypassing browser warnings. Certificate issuance is not automated by this package.

If UFW blocks access, find your actual subnet with `ip -4 route`. For **192.168.1.0/24 only as an example**:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8787 proto tcp comment 'Zedair home LAN'
```

Replace that subnet with yours. Do not enable UFW over SSH without preserving your SSH access. The installer makes no firewall changes.

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
| Instagram fails | Read Details; renew cookies, finish a browser challenge, wait for a rate limit or update engines as appropriate. |
| X video fails | Confirm it is a video status URL; check your session and update engines. Private/deleted/restricted posts can remain unavailable. |
| YouTube fails | Update engines, check Deno, inspect Details, and add YouTube cookies when required. |
| Threads finds no media | Embedded exact-post media is unavailable or changed. Experimental extraction cannot handle every login wall/page format. |
| Torrent offline after reconnect | Mount the SSD, then `sudo systemctl start zedair-transmission`. |
| File deletion blocked | Remove the owning torrent first, cancel the unfinished legacy job, or refresh if the file changed. |
| Speed test fails | Check laptop internet/DNS and endpoint reachability. Proxy/firewall restrictions or endpoint errors can prevent a test. No result is fabricated. |
| RAM limit reached | Reduce video quality or playlist size. One media worker and one torrent still share the 2 GB RAM. |

### Storage and partial-file cleanup

Media lives on the SSD. Account/queue state is on eMMC. History is limited to 1,000 jobs; clear finished history periodically. Keep only a few torrents loaded. Metadata and logs are bounded per job, but cancelled partials, engine caches and backups can accumulate.

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
```

This keeps files and configuration. To re-enable: `sudo systemctl enable --now zedair-transmission zedair` with the SSD connected.

## Where things live

| Path | Purpose |
| --- | --- |
| Your Git checkout, e.g. `~/Zedair-Download-Desk` | Source you pull from GitHub |
| `/opt/zedair` | Installed application, Python environment and Deno |
| `/etc/zedair` | Private service configuration and setup key |
| `/var/lib/zedair` | Account, login sessions, queue, preferences, cookies and torrent state |
| `/srv/zedair-ssd/Zedair` | Downloads on the USB SSD |
| `/var/backups/zedair` | Private backups made by the app upgrader |

The implementation uses Python’s standard library for the server, plain HTML/CSS/JS, yt-dlp, gallery-dl, FFmpeg, Deno and a private Transmission daemon. No Docker, Redis, Node server or browser automation runs on your laptop. The service has CPU/memory limits; these are safeguards, not a measured performance guarantee on your hardware.

## Development and checks

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile server.py media.py platforms.py publishing.py speedcheck.py install.py upgrade.py install-speedtest.py
```

With FFmpeg and `requirements.txt` installed in your development Python environment:

```bash
python tests/integration_media.py
python tests/integration_instagram_cookies.py
```

See `TEST-REPORT.md` for actual verification and its limits. This release was not installed on your physical laptop. Live account-specific Instagram/X access, real torrent swarms, SSD/systemd behavior and real laptop speed need confirmation there. No preview account, browser screenshot, cookie file or populated database is included in the release.

## Upstream documentation

- [yt-dlp and supported features](https://github.com/yt-dlp/yt-dlp)
- [yt-dlp cookies FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
- [Supported video extractors](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- [YouTube JavaScript runtime](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
- [gallery-dl](https://github.com/mikf/gallery-dl)
- [gallery-dl configuration](https://gdl-org.github.io/docs/configuration.html)
- [Transmission](https://github.com/transmission/transmission)
- [Deno](https://github.com/denoland/deno)
- [Ookla Speedtest CLI](https://www.speedtest.net/apps/cli)
- [Official Ookla x86_64 package and checksum](https://packagecloud.io/ookla/speedtest-cli/packages/ubuntu/jammy/speedtest_1.2.0.84-1.ea6b6773cf_amd64.deb)

The custom application is MIT licensed. Third-party tools retain their upstream licenses.
