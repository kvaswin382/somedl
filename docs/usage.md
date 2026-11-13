# Downloads, accounts and file management

[← README](../README.md) · [Setup](setup.md) · [Usage](usage.md) · [Maintenance](maintenance.md)

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

The interface includes a charcoal sidebar, light/dark themes, platform settings, a persistent background queue, image/video previews, list/grid file browsing, deletion, server CPU/RAM/storage gauges, and an **Ookla speed test run on the laptop**, with saved reports. Closing your browser does not stop work. Media concurrency is configurable from 1 to 20, with one browser at a time; torrents have their own queue limit.

## Add downloads and choose folders

1. Open **Downloads → Add downloads**.
2. Paste one webpage or direct file URL per line, up to 100. For X, use `https://x.com/USERNAME/status/POST_ID`; old `twitter.com` links also work. Instagram uses a `/p/`, `/reel/` or `/tv/` link.
3. Leave the destination empty for an automatic platform folder, or click **Browse** to choose/create an exact destination. To use the root itself, select it in Browse or check **Use Zedair root directly**.
4. Choose **Best available** for the highest available quality without a resolution cap, or an explicit ceiling such as 1080p. Use **MP4** for lossless container conversion where supported, or **Keep source container** to retain the original video container. Neither choice imposes a resolution limit. A source may not offer your preferred format/resolution.
5. Optionally enter a **Filename**. Leave it empty to use the source title. The separate display label only labels the queue job.
6. Leave **Website cookies** on automatic selection unless you deliberately need another profile.
7. Add to the queue. You may close the browser.

With destination `Travel`, a YouTube download lands directly in `Zedair/Travel`. Platform folders are appended only when no destination is selected. Change those default folder names in **Settings → Platforms & cookies** if wanted. For unknown video sites, the hostname is used unless you set a shared Other websites folder.


### Video format and highest quality

Use **Highest quality + MP4** by default. MP4 is an optional video container request, not an image extension or a lower-quality format filter. Original codecs are copied without re-encoding. If MP4 cannot hold those codecs, select Keep source container. Audio-only preserves the source audio codec. A direct file has no alternative quality variants to select; use its original webpage for that.

### Filenames and duplicates

Your optional filename is used first. If it has no extension, the actual output extension is added; a supplied known media extension is replaced by the actual output extension rather than mislabelling the bytes. Multiple outputs with the same name receive numbers. When the field is empty, a video’s source title becomes the filename when available. Instagram uses the available caption; content without a title/caption uses its source filename or post identifier. Names are shortened and cleaned for Windows/exFAT compatibility.

Example: `My video.mp4`, then `My video (1).mp4`, then `My video (2).mp4`. Existing files are never intentionally replaced. Multiple carousel items with the same caption are numbered too. Torrent filenames come from the torrent and follow Transmission’s normal resume/verification behavior.

Partial media and metadata live in hidden `Zedair/.incomplete/JOB_ID` folders. Completed new jobs publish media directly into the selected or default platform folder; there are no visible random job subfolders. Internal metadata is not offered in the file browser. Cancelled/failed jobs retain partial data for recovery; see storage cleanup in the linked guides.

Duplicate URLs are skipped within the batch and against non-cancelled queue/history entries, including completed or failed jobs. Common YouTube/share-link variants and tracking parameters are recognized, while meaningful query parameters are retained. To deliberately download a finished URL again, remove its queue record (or clear finished history), then add it. Existing SSD filenames still receive collision numbers.

### Queue controls and errors

- **Pause** stops the current engine and keeps partial files. **Resume/retry** reuses them where the source supports it.
- **Hold queue** pauses media work and active torrents. **Resume queue** releases the hold.
- **Remove from queue** stops the transfer and hides/removes that specific record. Downloaded files remain. **Clear finished history** removes completed/cancelled records only. Hidden partial files also remain on the SSD; removing a job loses its resume entry, so use Pause when you intend to continue later.
- **Move to front** changes the waiting order.
- **Details** shows the destination, current status and recent engine output.

Individual-download temporary failures use bounded retries: one initial attempt plus three retries by default, waiting 1, 2, 4… minutes, capped at 30 minutes. Recognized authentication, rate-limit, unsupported-media and access failures go directly to **Attention**. Fix the cause, then retry manually. A generic 403 does not prove the cookies are wrong. Instagram whole-profile jobs use the [long-collection cooldown policy](#long-instagram-collections-and-recovery).

Video progress usually measures the current stream, not a whole playlist. Merging can continue after 100%. Instagram shows engine activity, but not every gallery exposes a dependable percentage or ETA. Downloading the best quality does not involve video re-encoding; FFmpeg can merge separate audio/video streams.


## Tools: collect profiles

### Instagram

1. Save your Instagram cookies in **Settings → Platforms & cookies → Instagram** (instructions below).
2. Open **Tools → Instagram**. Enter `@username`, `username`, or its profile URL.
3. Select one radio option: **Posts** (images, carousel items and non-reel feed videos), **Reels**, **Everything**, **Images only** (all exposed images, including carousels), or **Profile picture** (highest available resolution).
4. Leave the optional destination empty for `Zedair/Instagram/<username>`, or enter e.g. `Collections` to save directly in `Zedair/Collections`.
5. Click **Queue profile**. Watch **Profile jobs** or the main download queue. **Details** has the engine log; the folder button opens its saved files.

Gallery-dl follows the profile's pagination until the accessible feed ends; there is no fixed post-count cap in this tool. Carousel media items are saved separately. Everything uses both feeds with a per-job media-ID archive to avoid downloading the same item twice. Stories and highlights have their own separate Tools form; tagged feeds are not included. A platform restriction or unavailable page can still prevent a complete collection.

Profile usernames are normalized to lowercase. Tools defaults to `Instagram/<username>` and `Facebook/<username>`; an explicit destination replaces this default.

### Facebook — optional, experimental

Direct Facebook video/reel downloads work through the main downloader and do **not** need this installation. For whole profiles, run these commands **once on Ubuntu after the app upgrade**:

```bash
df -h /
sudo bash /opt/zedair/install-facebook.sh
sudo systemctl restart zedair
```

The installer requires at least 1.5 GiB free on the system disk and installs the matched Playwright release, its Chromium headless shell and Ubuntu browser dependencies. Keep additional free space for normal operation. It does not run a browser permanently. Existing app upgrades preserve this optional installation.

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

## View the laptop's system usage

Open **Settings → System usage**. Circular gauges show whole-laptop CPU load, RAM, swap, internal system-disk usage and external SSD usage. Uptime, 1/5/15-minute load average, available RAM and CPU temperature (when the kernel exposes a recognized sensor) appear in the linked guides.

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

Cookies are account credentials: do not put them in GitHub, chat, screenshots or shared folders. They are stored under `/var/lib/zedair`, not on the removable SSD. Inputs are masked, values are not returned to the webpage, and recognized secret values are redacted from job logs. Only use this interface on a trusted LAN or configure HTTPS in the linked guides. Plain HTTP does not encrypt passwords/cookies in transit.

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

### Long Instagram collections and recovery

Retry an existing failed profile job after upgrading; its downloaded-media archive is preserved. To choose **Images only**, create a new collection with that option (it cannot retroactively change the selection of an old job). Every image in a mixed carousel is selected; videos and their preview thumbnails are skipped. **Images only** and **Profile picture** are separate radio selections, so they cannot conflict.

The worker reads one page at a time, records a checkpoint after every successfully processed page and keeps a media-ID archive. Failed items prevent advancing past that page. A restart replays the unfinished page and skips already saved media. Missing pagination metadata or missing carousel media is treated as incomplete, not silent success. If the web posts query returns structurally invalid/empty results, it can try a separate authenticated compatibility feed after a rest; this alternative also validates pagination. It is not used to get around authentication, challenge or rate-limit responses, and is not guaranteed to be accepted by Instagram. Reels still use the reels feed.

Completed-page fingerprints and previous cursors are stored on the SSD across restarts. If Instagram returns a previously completed page or cycles back to an earlier cursor, the worker stops with **pagination loop** and requires attention. It does not mark the collection complete or schedule an endless automatic replay. An unfinished page may be revisited to recover from a failed item, but the media archive skips already saved files. Do not repeatedly retry a pagination-loop error without checking the engine/log first.

Requests are randomly spaced by at least 10–25 seconds (a higher configured Instagram delay raises that range), and actual media downloads by 15–40 seconds. After about 15 minutes, the worker yields at the next completed page, publishes available files and rests for 2–5 minutes, allowing other queued jobs to run. A page with many items can take longer than 15 minutes. No fixed number of posts is imposed.

Transient profile/feed errors and rate limits use long randomized cooldowns: initially 30–60 minutes, growing to 4–8 hours, with up to 96 attempts before attention is required. Progress survives service restarts, and jobs can continue for days. Authentication and verification errors still need your attention. Do not repeatedly click Retry during a cooldown. A website can hide or remove content: “finished” means the available feed ended, **not proof that Instagram exposed every post**. Empty results do not become a successful archive. Private content still requires an account with access.

The queue uses a small progress-only request approximately once per second while a media job is running and the Downloads page is visible; only its active card is updated. Full status refreshes remain about four seconds apart, and hidden tabs slow to 15 seconds. No overlapping polling requests are started.

### File-operation details

Files stay within `Zedair`; paths outside it and symbolic links are refused. Active torrent-managed files must first be removed from the torrent queue without deleting their data. Copies use bounded 1 MiB buffers and check the space reserve; they may take a long time on USB 2.0. Keep the dialog open until completion. A batch validates all selected files before starting. If a runtime failure occurs partway through, the dialog reports what finished and leaves the remaining files selected; completed moves/copies are not rolled back. Copy/move/rename operations are serialized, and final download publication waits for a file operation to finish. Reads and normal progress polling continue. They are not resumable background copy jobs: a process/power failure may leave a hidden `.zedair-copy-*` temporary file, which can be inspected/removed manually after stopping the service. The source is retained on copy failure. Renames and moves preserve recorded download dates.
