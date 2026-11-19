# Storage, cloud and schedules

Use the normal upgrade commands in [README](../README.md). The upgrader also installs Ubuntu's rclone package and restricted systemd controls. Refresh with Ctrl+F5 afterward.

## Cloud destinations

1. Open **Settings → Cloud & backups → Add remote**.
2. Choose Google Drive or another installed rclone provider and answer its setup questions. On a headless server, OAuth providers can ask you to run `rclone authorize` on a computer with a browser and paste its result. Follow the displayed provider instructions; tokens stay in the laptop's private configuration.
3. To encrypt remote files, create a **crypt** remote over an existing cloud remote. Keep the encryption passwords somewhere safe: losing them prevents recovery.
4. Unfinished setup can be resumed after closing the dialog or restarting the app. **Remove** deletes only the local configuration; it keeps cloud files and refuses removal while jobs, schedules or another remote depend on it.
5. In **Add download → Save to**, select that remote. Browse only its folders; **New folder** creates a child folder. The saved rclone configuration is `/var/lib/zedair/rclone.conf`, owned by the service user. Existing remotes in a different user's rclone configuration are not silently imported.

Direct cloud transfers use bounded pipes, not the SSD/eMMC for media staging. There is one cloud download pipeline at a time. Individual HTTP files and extracted media streams are supported. MP4 streams use a fragmented MP4 container so they do not need a seekable local file. Original video/audio codecs are copied. A unique job/attempt suffix prevents overwriting an existing cloud file. Pausing or retrying restarts from zero; failed uploads are cleaned up where the provider permits it.

Provider limitations still apply. Unknown-length streams need a backend supporting streaming uploads. Backends that require local caching are refused; known-size direct files can use backends requiring a size. Playlists/collections should be submitted as individual items. Source extraction, incompatible codecs, site restrictions and remote quotas can still cause errors. There is no silent local-storage fallback. The SSD thumbnail/cover workflow applies to local files.

## Backups and reboot

**Backups** supports application/settings/database/cookies, SSD files, or both. Database snapshots are consistent; SSD files changing during backup cause the attempt to fail instead of being silently called complete. Backups stream as tar archives; application backup excludes the OS, virtualenv and browser binaries. Reinstall dependencies when restoring. Archive credentials are private: choose a crypt remote if encryption is needed.

Choose daily, weekly, monthly, yearly, or a five-field numeric cron expression and a time in **Asia/Kolkata**. The application evaluates the schedule; it does not install arbitrary shell commands in crontab. Missed runs while the server is off are not replayed. A date such as the 31st runs only in months containing that date. Retention deletes only successful backup paths recorded as created by this application, beneath its unique backup prefix. Other remote files are never swept by age. Progress and safe cancellation appear below the backup form.

**System usage → Scheduled reboot** uses the same calendar. Reboots are skipped while transfers, uploads, background workers or torrents are active, or torrent status is unknown. Only the fixed, root-owned systemd helper can request the reboot. Disable the schedule to remove it.

## SSD files

- **Upload files** accepts up to 20 files in a batch into the currently open subfolder, with individual progress. Existing filenames receive a number. Uploads stream in 1 MiB chunks.
- Select multiple files and choose **Delete**. A confirmation and stale-file check protect changed files. Files still owned by a torrent cannot be deleted, moved or rewritten here.
- Select an image and choose **Set folder cover**. The image stays in that folder; moving/deleting it removes the displayed cover.
- Video covers are extracted or generated in one size, using one FFmpeg worker. Compatible local MP4s receive an attached cover without re-encoding video/audio, if enough space remains for an atomic rewrite. Torrent-owned videos are never rewritten. Other containers still get a browser thumbnail. Automatic scans run in small batches while download workers are idle; **Storage → Scan videos now** also queues a batch.
- **Storage → Duplicate files** scans file content, not names. Review the result and retain the oldest modified copy, newest modified copy, or one manually selected file per group. Bytes are compared again before deletion. Active torrents and files changed after scanning are protected.

## Torrent resources and data

The torrent memory ceiling defaults to **1024 MiB**, configurable from **128–1536 MiB**. Concurrency is separate, **1–20**. These are ceilings, not memory reservations. Low peer/cache limits reduce baseline use; 1–2 active torrents is sensible on a 2 GB laptop. Memory pressure can still occur, especially with very large torrent file lists. Lowering the ceiling below current usage is deferred to avoid an immediate OOM: pause torrents, then save again. System usage shows the helper's result.

Tracker details show connected peers and the tracker's reported seeders/leechers; unknown counts remain blank. You can add trackers or request an announcement. Trackers cannot create peers for a dead torrent.

**Data usage** separates downloads (blue) from uploads (amber), including Transmission counters and rclone streaming bytes. Older history cannot be reconstructed. Figures are payload estimates, not ISP billing: retransfers count again, browser traffic and protocol overhead are excluded, and charts retain their existing UTC calendar.

## Diagnostics

**Activity** retains the latest 10,000 events and can be cleared. Request bodies, credentials and signed URL query strings are excluded. The detailed process toggle shows all processes, but Stop only addresses registered Zedair workers; it cannot kill arbitrary PIDs.

Reddit's public exact-post metadata routes are tried before browser discovery. The browser waits for memory headroom without weakening its sandbox. If Reddit blocks the server's IP/session on every route, new account cookies may not help; the app reports that restriction instead of substituting unrelated media. The project does not provide proxies or bypass DRM/login gates.

Telegram continues to send ordinary errors/snapshots only to the configured group and critical alerts to group/admin. Use **Test Telegram** on your laptop to verify real delivery. Unit tests cover failures, migrations, fallback and recipient routing; they do not establish that your bot currently has permission in your group.
