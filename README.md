# Zedair Download Desk — v2.1.0

A private, headless downloader for Ubuntu 24.04, Python 3.12 and a 2 GB x86_64 laptop with an external SSD. Submit original webpage links from the web interface; the laptop resolves and downloads their media in the background. Existing SSD files, accounts, cookies and queue history are preserved by the application upgrader.


## v2.1 stability and interface update

- Queue rows keep their favicon nodes across polling. Exactly one favicon or fallback is visible.
- Instagram `/s/<token>/` highlight shares are decoded into `/stories/highlights/<id>/`, including old queued jobs on retry.
- MP4 is the default **video** container; quality is independent. Photos keep their actual format. GIF animations are encoded to MP4 using one CPU thread; HLS/M3U8 streams are remuxed with their original codecs into MP4. GIF encoding uses CRF 18; it is not a lossless byte copy.
- General settings use server-described slider bounds and autosave. The download dialog has a live link count, compact primary controls and collapsed advanced options. All modal backdrops close the modal.
- Engine cards check stable PyPI releases in the background, cache results for one hour, and only offer an update when a newer release is known and the managed updater is installed.
- Data usage shows an activity chart and domain totals. Hourly data starts with this upgrade; existing daily history remains. These are partial payload estimates, not total network billing figures.
- GitHub Actions runs on `main`, `fix/**`, `improve/**` and pull requests, with real browser regressions and desktop/mobile screenshots.

## Upgrade from main or claude

This implementation starts from `main` and independently implements the useful `claude` changes. It does not merge that branch. From your existing checkout, run each command only after the preceding command succeeds:

```bash
git status --short
git fetch origin
git switch main
git pull --ff-only
sudo bash upgrade.sh
sudo bash /opt/zedair/install-browser.sh
sudo systemctl restart zedair
```

If Git reports local edits or a diverged branch, keep those edits and resolve that state before switching; do not force-reset the checkout. `upgrade.sh` creates a private backup in `/var/backups/zedair`, installs application files, checks startup and restores the previous app/state if the health check fails. It does not format, relocate or wipe the SSD. The browser installer installs a matched Playwright/Chromium pair and tests its sandbox as the service user. Refresh the web interface with **Ctrl+F5**; the footer should show **v2.1.0**.

Package updates are separate from application upgrades. Settings → Engines lists the application's Python packages and key system tools. Its Update buttons manage yt-dlp, gallery-dl and Playwright through a root-owned service with a fixed allowlist. Downloads pause during an engine update and recover when the app restarts. System tools remain Ubuntu-managed. A failed engine update attempts to restore the previous engine version; this is not a full dependency-environment rollback. The web process retains `NoNewPrivileges=true` and cannot submit arbitrary commands or package names.

## Download engine

1. Resolve supported Reddit mobile-share and Threads share links to canonical post URLs, with bounded redirects restricted to that platform.
2. Inspect actual response bytes and content type. Direct images and files use a bounded streaming HTTP downloader. **Video format settings never rename or convert images.** A JPEG stays `.jpg`, even if MP4 and a custom filename were selected.
3. Try maintained site extractors: yt-dlp and supported gallery-dl extractors, including image hosts. Threads also reads structured data for the exact requested post and selects its largest exposed rendition, without substituting recommendations or preview thumbnails.
4. Parse static player sources, video metadata and nested iframe pages. Explicit download links and supported image-host pages can also lead to file downloads.
5. When needed, launch one sandboxed Chromium instance. Observe network responses, rendered players, shadow roots and bounded embedded frames; try visible play controls and collect variants before selection. Link a playing low-quality URL to higher variants advertised by its player data. Inspect bounded HLS/DASH manifests instead of assuming the first playing URL is the best quality. Close Chromium before the file transfer.

The default is **Highest quality + MP4**. The video extractor requests the best available streams without an MP4-only filter or hidden 1080p cap. An explicit resolution selects a ceiling; MP4 output copies compatible original codecs into MP4 without re-encoding. Unsupported codec/container combinations report an error instead of silently selecting a smaller rendition. A direct file URL offers only that file's quality; submit the original webpage to discover other renditions.

**Refresh source & resume** is available in a failed/paused individual download's Details. It opens the stored original webpage again; no replacement CDN URL needs to be pasted. Failed transfers can also re-extract automatically. Progressive HTTP downloads append only after a strong ETag, matching length and correct byte range confirm the same entity. A changed or unverifiable entity restarts safely. Browser-discovered adaptive manifests restart when their previous representation cannot be validated; site extractors retain their native resume behavior. Expired direct CDN links cannot reveal a missing original webpage.

Website cookie persistence is optional, private and domain-scoped. Browser requests and file transfers share the relevant cookie jar, Referer and browser identity. **No cookies** disables saved-session reuse for that job. Deleting a platform's cookies also removes its saved browser sessions and prevents in-flight jobs from restoring them. Cookies improve continuity where a site accepts them; they do not guarantee exemption from bot detection. Login/verification, rate limits and DRM are not bypassed.

No extractor can promise every website. Ambiguous unrelated players, protected content, changed site APIs and some browser-only session mechanisms can still fail. The supplied Threads share/post and expiring CDN examples have not been confirmed as working live in this environment. See [TEST-REPORT.md](TEST-REPORT.md) for what was actually tested.

## Tools, queue and settings

- **Tools → Instagram stories & highlights:** submit a username/profile to collect currently accessible stories or all accessible highlights, or submit a particular story/highlight URL. A story URL collects that user's accessible current stories. Uses the saved Instagram session. Default destination: `Instagram/<username>`; a custom folder wins. Highlight owner metadata determines the default destination. Expired stories and content unavailable to that account cannot be recovered.
- **Concurrent downloads:** default 1, configurable 1–20 in steps of 1. Existing saved values remain in effect. Sliders autosave on release. Instagram/Facebook account jobs are serialized per platform, and all browser collectors share one cross-process slot. Individual browser-engine jobs also hold a separate single-job lease through download and link refresh. Browser startup checks both host and service-cgroup memory headroom (default 350 MiB), with a bounded discovery deadline. This is a safeguard, not a memory guarantee for arbitrary pages.
- **Queue:** Retry All retries failed entries; paused and completed jobs are untouched. Pagination, cached site favicons, detailed stage/error logs, and combined download speed when multiple downloads run. Cancel/remove affects only its own worker.
- **General settings:** SSD free-space reserve, torrent seeding ratio, retries, playlist limits, per-job media speed cap and separate torrent speed caps. The media cap applies to each media transfer, not a global bandwidth allocator.
- **Browser settings:** timeout, settling time, frame/candidate budgets, memory headroom, refresh attempts, cookie persistence and configurable tracker-host blocking. Tracker blocking can be disabled for a page whose player depends on a blocked host. Popups are closed; arbitrary advertisements are not clicked.
- **Telegram:** ordinary errors and browser screenshots go only to the configured group. Critical CPU, RAM, system-disk, temperature and low-SSD-space alerts go to the group and admin; group alerts can be pinned when the bot has permission. Thresholds, sustain time and per-condition cooldown are configurable with nonzero lower bounds. Default CPU/RAM/disk threshold is 90% for 60 seconds; temperature is 80°C; cooldown is 15 minutes. The SSD threshold follows the free-space reserve. Missing sensors are skipped. The token is masked in state responses. Text diagnostics redact cookies and signed URL queries. Screenshots are opt-in and can contain visible private page content. The Test button checks group delivery. Critical monitoring runs in the background when enabled and a bot is configured.
- **Data usage:** calendar day/week/month/year in UTC, grouped by submitted website. These are recorded transfer-payload estimates, not router/ISP totals. Browser traffic, protocol overhead, torrents and engines without byte-progress output are excluded; resumed engine counters may overcount.
- **Clear cache:** removes bounded diagnostic/favicon caches. Active stages, saved sessions and resumable partial files are retained. A separate confirmation can remove cancelled/orphaned working folders.

## Code map

| Area | Files |
| --- | --- |
| HTTP API, authentication, SQLite queue and concurrent workers | `server.py` |
| Media command construction and exact-post Threads parsing | `media.py`, `platforms.py` |
| Resolver and fallback orchestration | `download_engine.py`, `page_media.py` |
| Browser discovery and candidate ranking | `browser_capture.py` |
| Content typing, streaming and validated HTTP resume | `download_http.py` |
| Scoped sessions and global browser slot | `web_session.py` |
| Instagram profile and story/highlight collectors | `instagram_profile.py`, `instagram_story.py`, `profiles.py` |
| Safe publication and file operations | `publishing.py`, `fileops.py` |
| Settings, notifications and managed maintenance | `engine_settings.py`, `notifications.py`, `maintenance.py`, `engine_update.py` |
| Web interface | `static/index.html`, `static/app.js`, `static/style.css` |
| Installation and recoverable app upgrades | `install.py`, `upgrade.py`, `install-browser.sh` |

`browser_video.py` remains for compatibility and historical regression tests; new individual downloads run through `download_engine.py` and `browser_capture.py`.

### Long Instagram collections and recovery

Retry an existing failed profile job after upgrading; its downloaded-media archive is preserved. To choose **Images only**, create a new collection with that option (it cannot retroactively change the selection of an old job). Every image in a mixed carousel is selected; videos and their preview thumbnails are skipped. **Images only** and **Profile picture** are separate radio selections, so they cannot conflict.

The worker reads one page at a time, records a checkpoint after every successfully processed page and keeps a media-ID archive. Failed items prevent advancing past that page. A restart replays the unfinished page and skips already saved media. Missing pagination metadata or missing carousel media is treated as incomplete, not silent success. If the web posts query returns structurally invalid/empty results, it can try a separate authenticated compatibility feed after a rest; this alternative also validates pagination. It is not used to get around authentication, challenge or rate-limit responses, and is not guaranteed to be accepted by Instagram. Reels still use the reels feed.

Completed-page fingerprints and previous cursors are stored on the SSD across restarts. If Instagram returns a previously completed page or cycles back to an earlier cursor, the worker stops with **pagination loop** and requires attention. It does not mark the collection complete or schedule an endless automatic replay. An unfinished page may be revisited to recover from a failed item, but the media archive skips already saved files. Do not repeatedly retry a pagination-loop error without checking the engine/log first.

Requests are randomly spaced by at least 10–25 seconds (a higher configured Instagram delay raises that range), and actual media downloads by 15–40 seconds. After about 15 minutes, the worker yields at the next completed page, publishes available files and rests for 2–5 minutes, allowing other queued jobs to run. A page with many items can take longer than 15 minutes. No fixed number of posts is imposed.

Transient profile/feed errors and rate limits use long randomized cooldowns: initially 30–60 minutes, growing to 4–8 hours, with up to 96 attempts before attention is required. Progress survives service restarts, and jobs can continue for days. Authentication and verification errors still need your attention. Do not repeatedly click Retry during a cooldown. A website can hide or remove content: “finished” means the available feed ended, **not proof that Instagram exposed every post**. Empty results do not become a successful archive. Private content still requires an account with access.

The queue uses a small progress-only request approximately once per second while a media job is running and the Downloads page is visible; only its active card is updated. Full status refreshes remain about four seconds apart, and hidden tabs slow to 15 seconds. No overlapping polling requests are started.

### File-operation details

Files stay within `Zedair`; paths outside it and symbolic links are refused. Active torrent-managed files must first be removed from the torrent queue without deleting their data. Copies use bounded 1 MiB buffers and check the space reserve; they may take a long time on USB 2.0. Keep the dialog open until completion. A batch validates all selected files before starting. If a runtime failure occurs partway through, the dialog reports what finished and leaves the remaining files selected; completed moves/copies are not rolled back. Copy/move/rename operations are serialized, and final download publication waits for a file operation to finish. Reads and normal progress polling continue. They are not resumable background copy jobs: a process/power failure may leave a hidden `.zedair-copy-*` temporary file, which can be inspected/removed manually after stopping the service. The source is retained on copy failure. Renames and moves preserve recorded download dates.

## Previous fix: v1.3.1 Instagram profile downloads

The v1.3 profile command accidentally used gallery-dl's lowercase `--print`, which disables downloading. It still emitted “saved” messages and recorded media IDs in an archive, so the counter increased without any media files. The app then tried to publish an empty folder. Direct post/reel downloads did not use that flag, which is why they could still work with the same session.

The v1.3.1 fix used `--Print` (print **and download**); the v1.4 profile worker now uses gallery-dl’s Python download job API. It, ignores the old false-completion archive, resets affected false counters once, and repairs empty jobs stuck in the publication phase. It keeps existing files and cookies. Counts now come from the engine's after-download event; carousel items count as separate media files, not separate posts.

1. Put this updated source into your GitHub repository, commit and push it.
2. On Ubuntu, use the Git pull + `upgrade.sh` steps below. No new package or browser installation is required for this fix.
3. Reload the webpage and check the footer says **v2.1.0**.
4. Open **Tools → Profile jobs**, find the failed Instagram profile, and click **Resume / retry**. You can reuse the same job and saved cookies; do not delete SSD folders or replace a working session just for this bug.
5. Watch the media count and open its destination after the attempt finishes. Completed files are published under `Instagram/<username>`.

Old false counts may drop to zero after the upgrade: they did not represent downloaded media. The new archive is stored separately in the existing job's hidden working folder; the old archive is left intact and is not used for skipping. New jobs work with the same corrected command. If the website subsequently returns a real authentication/rate-limit error, inspect Details normally; this fix cannot override a website restriction.

The regression test reproduces the original false counter and archive with the real gallery-dl CLI, then upgrades the same job and verifies actual file bytes, publication, accurate counts and duplicate-free retry using local fixture media. It does not use a live Instagram account.

## Already installed? Update from GitHub

First copy the contents of this updated package into your GitHub project, commit and push them. Copy every supplied source file, including `profiles.py`, `facebook_profile.py`, `systemstats.py`, the optional installer/setup scripts, `upgrade.py`, `upgrade.sh`, `static/`, README and tests. Keep `server.py` and `upgrade.sh` together at the repository root. Do not commit cookies, private configuration, databases or backups.

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

For the new speed-test engine, run these **once if the Ookla engine is not installed yet** (the same steps apply to a first installation):

```bash
sudo bash /opt/zedair/install-speedtest.sh
sudo systemctl restart zedair
```

This downloads the official Ookla CLI from its publisher’s Packagecloud repository, verifies a pinned SHA-256 checksum and installs it locally. It does not run a test or accept agreements. In the webpage, open **Speed test**, review the first-use agreement and check it yourself before starting. Future app upgrades preserve this engine; you do not need to reinstall it each time. The rest of the downloader works without it.

After success:

```bash
systemctl status zedair zedair-transmission --no-pager
```

Refresh the page with **Ctrl+F5** on Windows. On your phone, reload the page. The footer should show **v2.1.0**. Existing jobs keep their old folders so they can resume; newly added jobs use your explicit destination directly or an automatic platform folder when none is selected. For a legacy individual-download job that uses an old folder layout, cancel it and add the URL again only if you want to change that layout. For the Instagram profile bug fixed in v1.3.1, retry the existing job as described above. Existing files are not relocated automatically.

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
| Instagram | Posts/carousels/reels; profile, story and highlight Tools | `Zedair/Instagram` or `Zedair/Instagram/<username>` |
| Facebook | Direct video/reel links; experimental profile collector in Tools | `Zedair/Facebook` or `Zedair/Facebook/<username>` |
| Reddit | Canonical/mobile-share posts and supported Reddit media | `Zedair/Reddit` |
| X.com / Twitter | Video posts and animated GIFs via a direct status URL | `Zedair/X` |
| Threads | Share-link resolution and exact-post media; live compatibility not guaranteed | `Zedair/Threads` |
| Other supported video sites | yt-dlp-supported URLs, including supported adult sites | `Zedair/website-domain` |
| BitTorrent | v1/hybrid magnets and `.torrent` files | `Zedair/Torrents` by default |

This is a downloader, not a website search directory. Availability depends on the source website and your session. DRM-protected media and X photo/feed crawling are not included. Profile collection only includes media the website exposes to your session; Facebook collection is experimental and cannot guarantee a complete archive. Threads support is experimental: login walls and changes to embedded page data can prevent downloads. It will not substitute profile pictures for missing post media.

The interface includes a charcoal sidebar, light/dark themes, platform settings, a persistent background queue, image/video previews, list/grid file browsing, deletion, server CPU/RAM/storage gauges, and an **Ookla speed test run on the laptop**, with saved reports. Closing your browser does not stop work. Media concurrency is configurable from 1 to 4, with one browser at a time; torrents have their own queue limit.

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
2. Paste one webpage or direct file URL per line, up to 100. For X, use `https://x.com/USERNAME/status/POST_ID`; old `twitter.com` links also work. Instagram uses a `/p/`, `/reel/` or `/tv/` link.
3. Leave the destination empty for an automatic platform folder, or click **Browse** to choose/create an exact destination. To use the root itself, select it in Browse or check **Use Zedair root directly**.
4. Choose **Best available** for the highest available quality without a resolution cap, or an explicit ceiling such as 1080p. Keep **Original format** for the best available source; select **MP4 · video only** for lossless container conversion where supported. A source may not offer your preferred format/resolution.
5. Optionally enter a **Filename**. Leave it empty to use the source title. The separate display label only labels the queue job.
6. Leave **Website cookies** on automatic selection unless you deliberately need another profile.
7. Add to the queue. You may close the browser.

With destination `Travel`, a YouTube download lands directly in `Zedair/Travel`. Platform folders are appended only when no destination is selected. Change those default folder names in **Settings → Platforms & cookies** if wanted. For unknown video sites, the hostname is used unless you set a shared Other websites folder.


### Video format and highest quality

Use **Highest quality + MP4** by default. MP4 is an optional video container request, not an image extension or a lower-quality format filter. Original codecs are copied without re-encoding. If MP4 cannot hold those codecs, select Original format. Audio-only preserves the source audio codec. A direct file has no alternative quality variants to select; use its original webpage for that.

### Filenames and duplicates

Your optional filename is used first. If it has no extension, the actual output extension is added; a supplied known media extension is replaced by the actual output extension rather than mislabelling the bytes. Multiple outputs with the same name receive numbers. When the field is empty, a video’s source title becomes the filename when available. Instagram uses the available caption; content without a title/caption uses its source filename or post identifier. Names are shortened and cleaned for Windows/exFAT compatibility.

Example: `My video.mp4`, then `My video (1).mp4`, then `My video (2).mp4`. Existing files are never intentionally replaced. Multiple carousel items with the same caption are numbered too. Torrent filenames come from the torrent and follow Transmission’s normal resume/verification behavior.

Partial media and metadata live in hidden `Zedair/.incomplete/JOB_ID` folders. Completed new jobs publish media directly into the selected or default platform folder; there are no visible random job subfolders. Internal metadata is not offered in the file browser. Cancelled/failed jobs retain partial data for recovery; see storage cleanup below.

Duplicate URLs are skipped within the batch and against non-cancelled queue/history entries, including completed or failed jobs. Common YouTube/share-link variants and tracking parameters are recognized, while meaningful query parameters are retained. To deliberately download a finished URL again, remove its queue record (or clear finished history), then add it. Existing SSD filenames still receive collision numbers.

### Queue controls and errors

- **Pause** stops the current engine and keeps partial files. **Resume/retry** reuses them where the source supports it.
- **Hold queue** pauses media work and active torrents. **Resume queue** releases the hold.
- **Remove from queue** stops the transfer and hides/removes that specific record. Downloaded files remain. **Clear finished history** removes completed/cancelled records only. Hidden partial files also remain on the SSD; removing a job loses its resume entry, so use Pause when you intend to continue later.
- **Move to front** changes the waiting order.
- **Details** shows the destination, current status and recent engine output.

Individual-download temporary failures use bounded retries: one initial attempt plus three retries by default, waiting 1, 2, 4… minutes, capped at 30 minutes. Recognized authentication, rate-limit, unsupported-media and access failures go directly to **Attention**. Fix the cause, then retry manually. A generic 403 does not prove the cookies are wrong. Instagram whole-profile jobs use the separate long-cooldown policy above.

Video progress usually measures the current stream, not a whole playlist. Merging can continue after 100%. Instagram shows engine activity, but not every gallery exposes a dependable percentage or ETA. Downloading the best quality does not involve video re-encoding; FFmpeg can merge separate audio/video streams.


## Tools: collect profiles

### Instagram

1. Save your Instagram cookies in **Settings → Platforms & cookies → Instagram** (instructions below).
2. Open **Tools → Instagram**. Enter `@username`, `username`, or its profile URL.
3. Select one radio option: **Posts** (images, carousel items and non-reel feed videos), **Reels**, **Everything**, **Images only** (all exposed images, including carousels), or **Profile picture** (highest available resolution).
4. Leave the optional destination empty for `Zedair/Instagram/<username>`, or enter e.g. `Collections` to save directly in `Zedair/Collections`.
5. Click **Queue profile**. Watch **Profile jobs** or the main download queue. **Details** has the engine log; the folder button opens its saved files.

Gallery-dl follows the profile's pagination until the accessible feed ends; there is no fixed post-count cap in this tool. Carousel media items are saved separately. Everything uses both feeds with a per-job media-ID archive to avoid downloading the same item twice. Stories, highlights and tagged feeds are not included. A platform restriction or unavailable page can still prevent a complete collection.

Profile usernames are normalized to lowercase. Tools defaults to `Instagram/<username>` and `Facebook/<username>`; an explicit destination replaces this default.

### Facebook — optional, experimental

Direct Facebook video/reel downloads work through the main downloader and do **not** need this installation. For whole profiles, run these commands **once on Ubuntu after the app upgrade**:

```bash
df -h /
sudo bash /opt/zedair/install-facebook.sh
sudo systemctl restart zedair
```

The installer requires at least 1.5 GiB free on the system disk and installs pinned Playwright 1.64.1, its Chromium headless shell and Ubuntu browser dependencies. Keep additional free space for normal operation. It does not run a browser permanently. Existing app upgrades preserve this optional installation.

1. In Firefox, sign into Facebook and open the intended profile/page normally.
2. Use **Shift+F9 → Cookies → facebook.com**. Copy **c_user** and **xs** values; optionally also **datr** and **fr**.
3. In **Settings → Platforms & cookies → Facebook**, save those fields, or import the site's Netscape cookies.txt file.
4. Open **Tools → Facebook**. Enter a profile/page username, numeric profile ID, or `https://www.facebook.com/profile.php?id=NUMBER` URL. Groups and arbitrary feed URLs are not accepted.
5. Select **Posts**, **Reels**, or **Everything** and queue the job. Files go under `Facebook/<username-or-id>`.

Posts visits the accessible posts/photos views and expands media links; Reels visits the reels view; Everything visits both. Photos use the largest exposed image matched to the specific photo ID, not a profile-avatar fallback. It scrolls with a delay, records discovered links on the SSD, and stops after repeated scrolls yield no new links. The **scroll limit** is a guard per view, default 2,000. Reaching it reports an incomplete attempt; increase it in the job's Tools card and retry if appropriate. Facebook may stop returning items earlier, change layouts or expose only part of a profile. A finished job means the collected accessible links were processed, **not proof of every historical post being archived**.

The browser uses the stored session and saves response-cookie changes after a successful job. It stops at detected login/verification requirements; it does not solve challenges. On your 2 GB laptop, large Facebook collections may be slow or reach a memory limit. The browser closes before video downloads, but torrents can still consume memory; pause torrents if needed. Do not disable Chromium's sandbox to work around a launch failure. Check the job log and Ubuntu's sandbox/AppArmor diagnostics instead.

### Pause, retry and long collections

A whole profile occupies one background media job. Its count grows with completed media; a percentage is not shown because the full total is unknown. Completed files become visible when an attempt finishes or stops with a downloadable partial result. A large collection can therefore take hours before publishing its files. Pause/retry preserves working files and the archive; a resumed discovery pass may start from the beginning, skipping items already recorded. Keep the same job to resume it.

A **new** profile job starts a separate collection and can download the same items again, using numbered names on collisions. Deleting a published file does not erase its ID from an unfinished job's resume archive. Re-add the individual post or start a new collection to fetch it again. Cancelling retains partials; remove them only when you no longer need recovery.

Authentication/rate-limit/verification failures need attention rather than repeated automatic attempts. Inspect Details, resolve the cause, then retry. No live Facebook or Instagram account was available during development; the collection logic is tested with fixtures, and real-account access must be checked on your laptop.

## Open the downloader at http://zed.local

After installing/upgrading, run **once on Ubuntu**:

```bash
sudo bash /opt/zedair/setup-local.sh
```

Then open **http://zed.local** on your phone/computer. No port is needed. Sign in once at the new hostname; browser login cookies for an IP address do not transfer to a different hostname. Your account, queue and saved platform sessions stay on the server. The old IP:8787 address remains usable.

This installs Avahi for multicast-DNS and a small systemd socket proxy from port 80 to the existing app port. It advertises `zed.local` without changing the Ubuntu machine hostname, and refuses an occupied port 80, a detected conflicting name, or an existing unmanaged proxy unit. It expects the app's normal HTTP configuration and refuses a TLS-enabled backend. It does not change the router or firewall and does not publish the service to the internet.

**Device support matters:** `.local` works when the client supports mDNS and the network allows multicast discovery. Phones on cellular data, guest networks, isolated Wi-Fi clients or a different VLAN may not reach it. It cannot be guaranteed on every device/router automatically. Test:

```bash
systemctl status avahi-daemon zedair-local.socket --no-pager
avahi-resolve-host-name -4 zed.local
curl -I http://zed.local
```

If the IP:8787 URL works but `zed.local` does not, check mDNS/client support and guest isolation. Reserve the laptop's IP in the router. For clients that cannot use mDNS, a router with local hostname/DNS support can provide a local address; configuration depends on that router. Do not enter a public DNS record for this private service.

If UFW is active, find your real LAN subnet using `ip -4 route`. Replace this **example** subnet with yours:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 80 proto tcp comment 'Zedair home LAN web'
sudo ufw allow from 192.168.1.0/24 to any port 5353 proto udp comment 'Zedair home LAN mDNS'
```

Do not enable UFW over SSH without preserving SSH access. No WAN port forwarding is needed. HTTP does not encrypt credentials; the HTTPS section explains the separate TLS option.

To undo only this optional address setup:

```bash
sudo systemctl disable --now zedair-local.socket
sudo systemctl stop zedair-local.service
sudo rm -f /etc/zedair/local-address-enabled
```

Your IP:8787 app still works. The original Avahi configuration is backed up as `/etc/avahi/avahi-daemon.conf.zedair-before`. If you have not made later Avahi changes, restore that file with `sudo cp /etc/avahi/avahi-daemon.conf.zedair-before /etc/avahi/avahi-daemon.conf` and `sudo systemctl restart avahi-daemon`. Do not blindly restore it over later intentional configuration. App rollback does not undo these optional system packages/units.

## View the laptop's system usage

Open **Settings → System usage**. Circular gauges show whole-laptop CPU load, RAM, swap, internal system-disk usage and external SSD usage. Uptime, 1/5/15-minute load average, available RAM and CPU temperature (when the kernel exposes a recognized sensor) appear below.

These readings come from Ubuntu on the **server**, never the phone/computer displaying the webpage. CPU is calculated from differences in Linux CPU counters across samples; the first sample may show a dash. RAM usage excludes memory Linux reports as available/reclaimable. Disk gauges describe the complete filesystem, including files outside Zedair; they do not expose those files. Missing SSD/sensors display unavailable instead of invented values.

The page refreshes roughly every four seconds while visible and backs off when the browser tab is hidden. It uses standard-library reads of `/proc` and `/sys`, with no extra monitoring daemon or continuous database history. These gauges are live usage, not a CPU benchmark or speed test. System readings are protected by the normal login.

## Settings and website login cookies

Open **Settings**. The submenu has **General & transfers**, **Platforms & cookies**, **Storage**, **System usage**, **Engines**, **Data usage**, and **Account & appearance**.

General settings include media/torrent speed limits, torrent seeding ratio, free-space reserve and retry count. Defaults are a 5 GiB SSD reserve, unlimited download speed, 256 KiB/s torrent upload and seeding ratio 0. KiB/s means kibibytes/second; 1,024 KiB/s is about 8.4 Mbps. The HTTP transfer path applies the media speed cap to Threads too. YouTube has its own playlist limit in its platform tab; the general playlist limit is the fallback for other video extractors.

Each platform tab has its folder, enable switch and applicable options. YouTube has quality, video-format, playlist and subtitle defaults. Facebook, Reddit, X and other video sites also expose their applicable format/quality defaults. Instagram has session requirements, best-stream/pre-merged video selection, resolution warnings and request delay. X has quality and an advanced API selector; keep **GraphQL** unless troubleshooting an upstream compatibility issue. Disabled platforms reject new jobs; they do not cancel existing ones.

The main form defaults to **Highest quality + MP4**. Saved platform quality/format settings are used only when you explicitly select **Use platform defaults**; those values are then copied into the new job. Cookie replacements and live engine options apply on the next attempt. Checking playlist/subtitle options in Add downloads also enables them when using platform defaults. To override platform defaults with those options off, select an explicit quality preset and leave the checkboxes unchecked.

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

You can use **Import cookies.txt** instead of the manual fields. YouTube, Reddit, Threads and Other websites use this import method. Facebook also accepts a cookies.txt import.

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

Open **SSD files** and enter a platform folder. Click an image/video filename marked **Preview** to open it inside the page. Videos have native playback, volume, seeking and fullscreen controls; supported audio files also have a player. Closing the preview stops playback. Use its Previous/Next buttons or left/right arrow keys to browse previewable files in the same directory, including across file-list pages. Click the separate **Download** button only when you want a copy on the current phone/computer. **New folder** creates a subfolder. **List / Grid** switches the layout and remembers your preference on that browser. Both layouts have Previous/Next pagination and a **Per page** selector for 24, 50 or 100 entries. **Sort files** offers filename A–Z/Z–A, size smallest/largest first, and date oldest/newest first. **Newest first is the default**; your selected order is remembered in this browser. Sorting covers the complete folder before pagination, and folders appear before files. Grid images load lazily from the real file, with the filename below; videos have a static placeholder and load only when you open Preview. Images are not server-generated thumbnails, so large originals can still use significant LAN bandwidth.

Date sorting uses the completion time recorded by v1.3.1 and later for newly published media. That remains correct even when the source website supplies an old file modification time. Older files, files imported through Windows and torrent-managed files use their filesystem modification time because their original download time was not recorded. Changing sorting does not rename files or change their timestamps. Download dates survive clearing queue history; replacing/renaming a file outside the app can make it use the modification-time fallback.

Previews require your normal login and stream the original file from the SSD. They do not convert video or use FFmpeg in the background, which keeps CPU/RAM usage low on the laptop. Your browser still transfers media bytes to display/play them. MP4 with H.264/AAC and common JPEG/PNG/WebP images usually have the broadest browser support. MKV, HEVC, some audio formats and unfinished torrent files may not play in your browser even when the download is valid. Use **Download** and a compatible player in that case. SVG/HTML files are not rendered inline. Seeking uses HTTP byte ranges; a disconnected SSD or expired login can interrupt playback.

Click **Delete** beside an individual file, check its full path, then confirm. **This permanently deletes it from the SSD; there is no recycle bin.** **Delete folder** permanently removes the currently opened subfolder and all its contents after an in-page confirmation showing its file count and size. The confirmation is rejected if the folder changed while you were reviewing it. Active-job/torrent folders, the Zedair root, symlinks and nested mounts are protected. There is no recycle bin; verify the path before confirming. Files outside `Zedair`, hidden working folders and symbolic links are not accessible.

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

Reserve the laptop’s IP in your router’s DHCP settings so its URL stays stable. Phone/computer and laptop must be on the same LAN; guest Wi-Fi isolation can block access. Do not forward web ports **80 / 8787** or torrent RPC port **9092** to the internet.

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
| Instagram profile shows a count but no files | Upgrade to v2.1.0, then retry the same profile job; see the recovery section at the top. Existing working cookies need no replacement for this bug. |
| Website DNS lookup fails | Run the hostname checks near the top on the laptop; other sites working does not establish that this domain resolves. |
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

## Development and checks

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile server.py media.py platforms.py publishing.py speedcheck.py systemstats.py profiles.py facebook_profile.py setup-local.py install.py upgrade.py install-speedtest.py
```

With FFmpeg and `requirements.txt` installed in your development Python environment:

```bash
python tests/integration_media.py
python tests/integration_instagram_cookies.py
python tests/integration_profiles_formats.py
python tests/integration_instagram_profile_download.py
python tests/integration_profile_large.py
python tests/integration_browser_handoff.py
python tests/integration_page_discovery.py
python tests/integration_profile_loop.py
python tests/integration_automatic_engine.py
python tests/integration_instagram_stories.py
python tests/integration_browser_capture.py  # requires matching sandboxed Chromium
```

See `TEST-REPORT.md` for actual verification and its limits. This release was not installed on your physical laptop. Live account-specific Instagram/X access, real torrent swarms, SSD/systemd behavior and real laptop speed need confirmation there. No preview account, browser screenshot, cookie file or populated database is included in the release.

## Upstream documentation

- [yt-dlp and supported features](https://github.com/yt-dlp/yt-dlp)
- [yt-dlp cookies FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
- [Supported video extractors](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- [YouTube JavaScript runtime](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
- [gallery-dl](https://github.com/mikf/gallery-dl)
- [gallery-dl configuration](https://gdl-org.github.io/docs/configuration.html)
- [Playwright browser installation](https://playwright.dev/python/docs/browsers)
- [Avahi](https://github.com/avahi/avahi)
- [Transmission](https://github.com/transmission/transmission)
- [Deno](https://github.com/denoland/deno)
- [Ookla Speedtest CLI](https://www.speedtest.net/apps/cli)
- [Official Ookla x86_64 package and checksum](https://packagecloud.io/ookla/speedtest-cli/packages/ubuntu/jammy/speedtest_1.2.0.84-1.ea6b6773cf_amd64.deb)

The custom application is MIT licensed. Third-party tools retain their upstream licenses.

## Upstream references

- [gallery-dl Instagram configuration and pacing](https://gdl-org.github.io/docs/configuration.html)
- [yt-dlp releases and extractor updates](https://github.com/yt-dlp/yt-dlp/releases)
- [Playwright network events](https://playwright.dev/python/docs/network)
- [Ubuntu restricted user namespaces](https://ubuntu.com/blog/ubuntu-23-10-restricted-unprivileged-user-namespaces)
