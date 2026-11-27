## v2.3.4 Instagram fallback validation

New regression cases cover empty gallery success, failed extractor handoff, whole-transfer browser leasing, exact carousel matching, recommendation rejection, video-poster rejection, missing carousel items, image/video rendition selection, inline DASH audio/video selection, story IDs, highlight catalogs, profile ownership, archived collection completion and disabled fallback. The sandboxed Chromium integration uses controlled Instagram responses to exercise authenticated JSON, HttpOnly cookies, rendered carousel navigation and login-wall errors. These fixtures do not establish live Instagram account compatibility.

# v2.3.3 fresh failed retries and LAN streaming

Manual and bulk retry share the fresh-retry path for failed items. Worker-side preparation discards only the job's partial workspace and publication journal, resets the publish phase and per-job gallery cache/checkpoints, and leaves finished files and account cookies intact. Attempts remain cumulative; a separate retry baseline renews the automatic retry budget. Paused resume and explicit refresh/resume retain partials. Tests reproduce a stale publish-phase failure, execute extraction on retry, verify counts and finished-file preservation, and redownload through a real gallery-dl fixture after resetting its archive.

Streaming binds to all IPv4 interfaces when selected, retaining LAN-source checks, authentication and read-only access. Address discovery handles the service PATH and has a route-based fallback. API/UI show usable LAN links, ports, remote folder, username and buffer details without returning passwords. The real rclone fixture accesses the listener through detected non-loopback LAN addresses and checks exact range bytes. This does not test the user's router/firewall or live Instagram account.

# v2.3.2 visible bulk retry

Retry all failed is beside the queue filters and works across search and pagination. The API reports queued/skipped counts, excludes removed jobs and waits for stopping workers. The button prevents duplicate submissions and reports errors without losing its enabled state. Tests verify Hold queue, paused/completed jobs, stopping workers, repeated requests and a browser click with twelve failures and an active search.

# v2.3.1 remove GUI engine installation

The web update endpoint now returns HTTP 410 without launching a process. The engine page only reads versions and displays/copies fixed terminal commands. Per-engine update commands require a newer stable version; missing engines get install commands even if release checks are unavailable. The combined command requires all three installed engines to have updates. Playwright commands include matching Chromium installation and sandbox checks.

The privileged Python updater is removed. Install/upgrade revokes its polkit permission, masks the service template and removes its installed script/marker, retaining diagnostics. It refuses to interrupt an active legacy updater. The old shell updater is inert. Tests cover command availability, missing Playwright, invalid/offline metadata, HTTP rejection, retirement and browser controls. No actual package installation or physical laptop crash is reproduced; the earlier fixture success did not establish safety on the user's hardware.

# v2.3.0 maintenance, rclone and file management

The updater now quiesces the socket-activated `zed.local` forwarder before stopping the app, and restores its previous listener state after success, failure or recovery. Service-stop errors are inside recovery handling and retain their actual message. The systemd integration fixture runs on isolated temporary units under reconnect polling; no engine packages or real Zedair units are changed.

Rclone config import merges new remotes without overwriting existing credentials. Upgrade can import the invoking sudo user’s regular config; encrypted files can be imported with a transient password in the UI. HTTP/WebDAV streaming runs behind an authenticated LAN listener with two response slots, a shared 8–512 MiB read-ahead budget and disabled rclone disk/file buffers. Password hashes are private and omitted from APIs. Actual rclone tests check unauthenticated rejection, full reads, exact range reads, WebDAV listing, write rejection and shutdown without SSD cache. Remote copy tests verify exact transferred bytes and preservation of every existing destination, including conflicting contents. These are protocol tests; VLC itself and Google Drive authorization are not exercised.

Gallery-dl payload metering includes ordinary HTTP and native yt-dlp downloads; archived files do not count again. Torrent counters use owned torrent downloadedEver/uploadedEver deltas with fresh-add baselines, excluding totalSize/haveValid and historical daemon counters. Global IANA timezone controls schedules, display dates and usage aggregation, including half-hour day boundaries. Old hourly/daily measurements remain approximate. Folder rename checks ownership/workers and recovers metadata from an atomic-rename journal; covers and recorded dates follow the new path, including journal replay after a remount. User-specified filenames still take precedence over Facebook creator/caption metadata.

Validation includes unit tests, actual gallery/rclone fixtures, the required sandboxed browser UI suite and the isolated systemd integration in CI. No claim is made of testing the physical Atom laptop, live social accounts or the user’s private cloud.

# v2.2.6 torrent batches and duplicate confirmation

Added one-selection batches of up to 20 torrent files, processed sequentially with individual results and continuation after failures/skips. Only one metadata file is encoded and sent at a time. The page must remain open until all additions finish; Transmission then handles leeching independently. New torrents are added paused and started explicitly after a successful addition, respecting the paused option and queue limits. Duplicate responses never automatically restart or relocate existing entries.

A time-limited confirmation token permits a fresh-copy action only for the same existing ZedAir torrent/location. It requires the same uploaded metadata, creates an exclusive empty subfolder, stops/removes only the queue registration with delete-local-data=false, re-adds paused, verifies and optionally starts it. Transmission refuses simply relocating a completed entry to an empty folder as missing data; the real-engine fixture caught that behavior before publication. Old data stays untouched; file selections are retained. Repeat confirmations return the original result. Errors attempt to restore the old queue entry paused using the same metadata and retained file selection; all file data is preserved. Duplicate magnets are left unchanged and request a .torrent file for a fresh copy.

Unit tests cover duplicate non-mutation, old-file integrity, explicit confirmation, pause, stale/expired tokens, ownership, hold, folder collisions, replay and failure handling. The browser fixture selects 20 files, rejects 21, skips one duplicate, confirms another, continues after a bad file and verifies individual results. A loopback-only real Transmission fixture verifies completed old bytes are preserved while the confirmed new location starts at zero completion, with one torrent entry. No public torrent swarm is contacted.

# v2.2.5 torrent peer controls

Added autosaving peer sliders, sharing server-side ranges: global 10–1,000 and per torrent 10–150, step 1. Defaults are 64 and 20. Removed the memory-derived global cap and hardcoded per-torrent overrides. Saved values apply to Transmission session settings, new torrent additions and existing ZedAir-owned torrents. Empty ownership lists never send an unscoped torrent-set. The existing monitor retries after connection failures and applies settings at startup.

Regression coverage checks both endpoints and invalid values, maximum values under a 128 MiB memory setting without clamping, existing torrent ownership, the settings HTTP API and new torrent arguments. Browser coverage checks slider limits, keyboard autosave and values after reload. RPC payloads are verified with a simulated Transmission service; this does not test live swarm connectivity.

# v2.2.4 file uploads

Replaced browser Base64/JSON conversion per 1 MiB with raw binary chunks of at most 4 MiB, sent directly from file slices. Uploads show byte progress, average speed, and a separate saving phase; the file picker is disabled during a batch to prevent overlapping progress rows. JSON control requests and legacy 1 MiB chunks remain supported. No transfer sleeps or bandwidth cap are used. Network reads happen before global storage locks, with authentication/CSRF/origin checks and fixed body bounds. Partial chunks are rejected before appending, offsets and free space remain checked, and publication still flushes the file without overwriting an existing filename.

Five real HTTP regressions cover multi-chunk byte integrity/collisions/replayed offsets, authentication and body limits, interrupted reads, responsiveness during a slow read, and free-space rejection. The browser fixture uploads a multi-chunk binary, a text file and an empty file, checks exact saved bytes, request sizes and completed speed labels. These tests do not measure the user's Wi-Fi or Atom laptop throughput.

# v2.2.3 toast visibility

Toasts are fixed at the top with z-index 2147483647 and use the browser top layer above native dialogs. They follow the most recently opened dialog and return to the page when it closes. Success/error/warning/info have distinct green/red/amber/blue palettes in both themes, text labels, live announcements and a dismiss button. Error and validation call sites select their type explicitly. The browser fixture checks foreground hit-testing, nested dialogs, focus preservation, dismissal and distinct colours on desktop/mobile in both themes.

# v2.2.2 stability validation

Regression coverage exercises asynchronous duplicate deletion and progress, cancellation before removal, batch ownership checks, responsive folder/task reads while verification is waiting, concurrency reduction with reserved worker slots, and settings saves independent of Transmission availability. File-browser tests check metadata work is limited to the visible page and preview polling does not rescan directories. Corrupt videos are backed off so later files can get previews. Idle torrent counters no longer cause database writes.

The real FFmpeg/rclone integration checks preview-only generation preserves the original video bytes, followed by explicit cover embedding without re-encoding. The browser fixture clicks duplicate deletion through confirmation, observes live progress and completion, and checks an actual video thumbnail appears automatically in list and grid views. No Atom-laptop latency measurement or guarantee of zero remaining bugs is claimed.

# v2.2.1 validation

The main download form has no remote fields and makes no rclone request. Settings → Rclone owns remote setup, URL downloads, remote folder selection/creation and backups. The browser regression checks the empty-remote state, selected remote/subfolder in a cloud submission, unique URL counting and SSD-only local submission, while retaining desktop/mobile backdrop checks. Existing engine and storage integrations remain enabled.

# v2.2.0 validation

- The download dialog opens before loading cloud destinations. The browser fixture holds that response until after dismissal, then verifies that completing the lookup cannot reopen the dialog or block Settings. Mobile backdrop checks explicitly wait for the dialog to open first.
- 198 unit tests passed locally, including new URL cleanup, exact duplicate revalidation, upload offsets/collisions, remote confinement, cron timezone, torrent counter resets and managed-worker checks.
- Real FFmpeg tests cover VOD and live-style HLS (a playlist initially lacking ENDLIST), actual MP4 container detection, original H.264/AAC resolution and GIF conversion. JPEG bytes remain unchanged with MP4 selected.
- A real rclone/WebDAV fixture verifies remote folder browsing, exact uploaded bytes with no app media staging, a single thumbnail, MP4 attached cover, and streamed tar backup.
- Existing media, Instagram, profile, page discovery and browser handoff fixtures passed locally. Browser launch requires the GitHub runner's sandbox integration; CI runs browser capture and the expanded desktop/mobile UI fixture.
- No user cookies, Telegram token, Drive account, real reboot or live package update is used by these tests. Those deployment-specific integrations require the installed Ubuntu service and real account permissions. Reddit IP-block bypass is not claimed.


Ten new regressions cover inconsistent engine ETAs, speed changes, resumed offsets, stream/counter resets, unknown and invalid rates, estimated sizes, completion, stale snapshot values, fallback/conversion, and a real direct-transfer loop with deterministic slowing response data. The UI integration checks full-state and fast-poll consistency, stale/finishing text, and seconds/minutes/hours/days formatting. ETA is current-file remaining bytes divided by the displayed rate; merging and whole-playlist duration are not predicted. Live site transfer speed can still fluctuate.

# v2.1.2 UI and Telegram verification

The local suite passes 174 unit tests, including Telegram HTTP error parsing/redaction, non-JSON errors, rate limits, timeout handling, bounded group migration, and rejected-photo fallback. All Telegram transport tests are mocked; no real messages or private tokens were used. The real-browser regression also checks white destructive-button text on normal/hover states, gray folder icons, select text/arrow spacing, desktop/mobile light/dark layouts, and saving the visible Telegram fields before a group-only test with routine notifications disabled. The push workflow runs these alongside the existing 14 integration scripts, syntax and checksum checks.

The reported `HTTP 400` alone does not identify the user's live Telegram failure. This release exposes Telegram's actual description, fixes stale test settings, and handles confirmed group migrations/image rejection. Live group delivery and bot permissions still require the laptop's Test button. README now links separate setup, usage, maintenance and development guides; installation, rollback, SSD, cookies, network and recovery instructions are retained.

# v2.1.1 interface verification

Changes are limited to restoring the `873313e` blue/neutral palette, centering Settings, correcting slider focus decoration and compacting the engine inventory. The existing real-browser regression now asserts the original light/dark color values, equal settings margins on wide and mobile screens, no track outline for pointer/keyboard range use, and compact engine rows. CI captures light/dark desktop and mobile screenshots for review. Existing extraction, queue, alert and media tests remain enabled.

# Verification report — Zedair 2.1.0

Built from the user's installed `873313e` revision. No live account credentials, Telegram messages, or laptop service changes were used during development.

The prior candidate passed **163 unit tests in CI**; the final suite adds a real file-lock contention/heartbeat regression, JavaScript syntax and Python compilation passed, and **12 non-browser integration scripts passed**. The additional real HLS/GIF test verifies H.264/AAC codecs and source dimensions, MP4 output, odd-dimension GIF padding, and exact unchanged JPEG bytes despite MP4 being selected.

New regressions cover the supplied Instagram highlight share through actual gallery-dl extractor construction, concurrency 1–20 step 1, nonzero alert bounds, sustained usage/cooldown, missing sensors, low SSD space, group-only diagnostics, critical group/admin routing and group-only pinning, stable release comparisons, and hourly/daily usage totals.

The release workflow runs all integration scripts, including real sandboxed Chromium extraction and UI tests. UI checks cover a single persistent favicon, live link counts, default MP4, slider autosave, masked tokens, release-aware engine cards, charts, highlight submission, backdrop close, and mobile/dark layout overflow. Screenshots are attached as CI artifacts. [The verified candidate workflow](https://github.com/kvaswin382/somedl/actions/runs/43333242914) passed all 163 unit tests and all 14 integration scripts, including the real Chromium/UI tests, JavaScript syntax check and file checksums. Desktop and mobile screenshots were inspected. The final mobile refinement reduces navigation/summary height so queued jobs appear in the first screen, and removes in-progress CSS transitions from screenshots. An additional regression delays the first status response and opens Settings during reload, checking that controls initialize once data arrives. Telegram delivery status now refreshes alongside normal status polling. A real file-lock contention test checks that waiting browser jobs report activity until the slot is released, avoiding the worker inactivity timeout during long transfers. The final branch/main workflow repeats these checks on push.

Live Instagram availability still depends on the account/session and Instagram's response; the exact base64 exception is reproduced and fixed without claiming a live account download. Hardware load on the Atom laptop, Telegram delivery/pinning permissions, real systemd upgrades and physical SSD behavior require checking on the laptop. See the prior report for the inherited engine/security fixture scope.

---

# Verification report — Zedair 2.0.0

Revision date: 12 November 2026. Target: Ubuntu 24.04 x86_64, Python 3.12, 2 GB RAM and external exFAT SSD.

## Scope

Implemented independently from main `3a0dd2e954a3f37ecdbdb670363585686b28e7d2`, after reviewing the claude branch at `ed86b688d31c02641c492a6004807ebd3119f654`. No live account credentials were used, Telegram messages sent, or laptop services changed during development.

## Automated coverage

**143 automated tests passed locally**. The release workflow also requires all **13 integration scripts**, including actual sandboxed Chromium and browser UI checks. The unit suite includes the previous 101 tests plus new regressions for:

- Actual image bytes overriding MP4 filenames/settings; direct non-media file publication.
- Renewed URL resume with matching strong ETag/range/length; changed entities and weak validators restart; error HTML never becomes a downloaded file.
- Adaptive master selection, quality ceilings, ambiguous-player rejection and matching the playing rendition to higher JSON variants.
- Cross-origin credential stripping, private redirect rejection and canonical Reddit/Threads share resolution.
- Private scoped cookie persistence, host-only domain boundaries, deletion/revocation, concurrent merges and masked Telegram tokens. Browser fixtures also check that HttpOnly cookies remain inaccessible to page scripts and survive persistence.
- Instagram story/highlight targets, owner folders and platform enable switches.
- Concurrent worker cancellation, cache preservation, usage accounting and fixed update allowlists.
- Authentication/CSRF on new APIs, Retry All behavior and masked-token preservation/clearing.
- Existing installer upgrade/rollback fixtures preserve private state and environment.

The latest complete checks run through [GitHub Actions](https://github.com/kvaswin382/somedl/actions/workflows/verify.yml). Release requires every stage to pass, including actual browser execution.

## Real-engine local integrations

The eight pre-existing integration scripts passed using actual yt-dlp/gallery-dl/FFmpeg and local HTTP fixtures. `integration_profiles_formats.py` now verifies selection of 4320p WebM plus audio ahead of 2160p MP4, and respects an explicit 1080p ceiling.

The new pipeline and story integrations passed:

- `integration_automatic_engine.py`: the production orchestration follows a nested player, selects the highest labelled rendition, keeps title and exact bytes, carries browser cookies and Referer to a protected fixture CDN, and inspects a bounded HLS master. **Browser discovery is substituted in its handoff scenario.**
- `integration_instagram_stories.py`: actual gallery-dl child jobs follow two highlight collections, fetch exact image bytes, emit owner/completion metadata and skip archived IDs on retry. **Instagram responses are fixtures.**

`integration_browser_capture.py` separately launches actual sandboxed Chromium, clicks a player inside an iframe, matches JSON variants to playback resolves a lengthless HLS master, preserves request-specific authorization without copying API tokens to discovered URLs, and preserves HttpOnly cookie flags. It exits 77 when Chromium is unavailable, and fails instead when `ZEDAIR_REQUIRE_BROWSER=1` (CI). It does not weaken the Chromium sandbox. Its result must not be conflated with mocked browser handoff tests.

## Environment limits

This workspace could not install the matching Playwright headless shell; the download failed. The available system Chromium also failed to launch under workspace IPC restrictions. Actual Chromium discovery could not run locally. It **passed on Ubuntu 24.04 in GitHub Actions** after applying the same per-executable AppArmor namespace permission as the installer. Chromium sandboxing and the global Ubuntu restriction remained enabled. The workflow requires this test rather than silently skipping it.

The supplied Threads share and canonical post were inaccessible to the research tool. No end-to-end success is claimed for that live post, Reddit mobile links, live Instagram stories/highlights, expiring bigcdn URLs or arbitrary public websites. Cookies, player structure and source availability can differ on the laptop.

Additional tests exercise lossless MP4/audio conversion with identical encoded-packet hashes, updater rollback and recovery after interruption. The browser UI fixture passed setup, queuing, settings, package inventory and Instagram Tools using fake credentials; it does not start download workers.

No physical Atom/2 GB performance, exFAT unplug, live torrent swarm, Telegram delivery or full systemd/polkit installation was exercised. Unit/fixture tests cover their application interfaces, not actual hardware deployment. Installed dependency interfaces used for testing: Python 3.12, yt-dlp 2026.10.26, gallery-dl 1.32.19 and Playwright 1.65.1.
