# Verification report — Zedair 1.4.0

Revision date: 9 November 2026. The application version remains 1.4.0.
Target: Ubuntu 24.04 x86_64, Python 3.12, 2 GB RAM, exFAT USB SSD.

## Current verification

**101 automated tests passed** with `python3 -m unittest discover -s tests`.
They include the earlier authentication, cookies, queue, storage, preview, speed-test, upgrade/rollback and profile regressions, plus:

- Highest-quality MP4 defaults, duplicate detection across batch/history and common share-link variants, and explicit removal before a deliberate finished-job re-download.
- HQPorner mobile URL normalization and bounded temporary DNS retries with domain-specific diagnostics.
- Static player/iframe parsing, ranked MP4 selection, ambiguity rejection and forced browser retry behavior.
- Persistent engine markers, missing-browser diagnostics and refusal to change the engine of an already running job.
- Running-job removal stops its process, removes its queue record and retains completed files.
- Persistent completed-page fingerprints and previous-cursor history, including detection after reopening the SQLite history.
- Images-only radio API mapping and independent media-selection duplicate checks.
- Batch copy/move, preflight validation, collision numbering, stale-file rejection and date preservation.
- Recursive folder deletion requires a current inventory confirmation, protects the root, symlinks, active download destinations and torrent files, and rejects changes made during confirmation.
- Authentication on the new file endpoints and HTTP dispatch of inspect/confirmed recursive deletion.

## Real-engine integrations

All eight scripts passed. Network media was served by local fixture servers; no real account cookies or live social-site media were used.

1. `integration_page_discovery.py`: follows a nested iframe through real HTTP, selects the highest labelled MP4, retains the top-level page title and embedded-page Referer, and uses real yt-dlp to download identical media bytes. Chromium is explicitly unavailable in this fixture, proving the static fallback can work without it.
2. `integration_browser_handoff.py`: real yt-dlp receives a detected URL, page title and Referer, saves exact MP4 bytes and removes its temporary cookie jar. **Browser discovery itself is substituted**; this is a handoff test, not a live Chromium/player test.
3. `integration_profile_large.py`: real gallery-dl processes 300 posts over 30 pages, saving 330 exact-byte images including every image in mixed carousels. No video or low-resolution thumbnail request occurs. A forced mid-page failure is resumed without repeated media downloads; a completed retry makes no further media requests.
4. `integration_profile_loop.py`: exercises gallery-dl's actual GraphQL pagination with controlled response data. After two worker restarts, the server repeats the first completed page under a new cursor. The worker returns its loop-stop status before requesting that page's media again. Two images are fetched once each; the collection stays incomplete.
5. `integration_instagram_profile_download.py`: reproduces the old lowercase `--print` false-count/poisoned-archive bug, recovers the same job through the current worker, publishes four exact-byte files over two pages, and verifies archive reuse on a simulated interrupted-job retry.
6. `integration_instagram_cookies.py`: real gallery-dl cookie serialization, session-expiry-zero handling and simulated cookie rotations survive write/reload cycles.
7. `integration_profiles_formats.py`: real gallery-dl pagination/archive behavior and real yt-dlp highest-compatible-MP4/quality-ceiling selection.
8. `integration_media.py`: real yt-dlp transfer, title/progress/metadata output, publication and numbered collision handling with FFmpeg available.

## Interface checks

An isolated preview with generated sample media and disabled download workers was used. It is not part of the release.

- The main form visibly defaults to Highest quality and MP4.
- Queue filters show non-zero counts; a fixture whose persisted stage is browser shows **Browser engine**.
- Selecting three files and copying them through the bulk dialog completes and reports three files copied.
- Instagram's five radio options render correctly; Images only queues a job with the correct flag.
- Desktop video preview loads the generated MP4. After a sizing correction, its rendered dimensions were 1158 × 651.375, matching the source's 16:9 ratio rather than leaving side gutters around an intrinsic-size player.
- File list/grid and the selection toolbar were inspected in a narrow frame with a 320 CSS-pixel content viewport. Controls wrap into separate rows. The bulk dialog measured 303 pixels wide, with equal scroll/client widths of 301 pixels, so its content did not overflow horizontally.
- Folder deletion, queue removal and stale-inventory protections are covered by API/process tests. No user data was deleted through the browser during QA.
- JavaScript syntax, Python compilation, shell syntax, package file lists, manifest checksums and ZIP integrity were checked before delivery.

## Limits and unverified behavior

The supplied Eporner and HQPorner hosts did not resolve from the execution workspace. This does **not** establish why the user's laptop cannot resolve Eporner, nor does it prove those sites are down. Their exact live pages were not verified. The README includes laptop-side DNS diagnostics and distinguishes DNS failure from extraction failure.

The execution environment prevented local Chromium startup with an IPC `socket() ... Operation not permitted` error. Its sandbox was not disabled. Actual on-demand Chromium media discovery, current live player layouts, Ubuntu AppArmor installation and memory usage on the 2 GB laptop remain unverified. The UI preview ran in a separate browser; that does not validate the server-side browser worker.

No live Instagram/Facebook account, real torrent swarm, physical SSD, Zedair server or GitHub repository was accessed. The package was not installed on the laptop or pushed to GitHub. Hardware behavior, exFAT power loss, systemd/mount/Avahi configuration, LAN-wide `zed.local`, account restrictions and actual speed measurements still need target-machine checks.

Page/cursor detection prevents the tested replay loops; it cannot force Instagram to expose inaccessible, deleted or hidden posts. A failed page is intentionally replayed for recovery, while the media archive prevents downloading already completed items again. Completion means the exposed feed ended, not proof of a full account archive. Cookie refresh preserves response cookies; it cannot force a service to renew a login.

MP4 means the best matching source, not transcoding unavailable formats. Multiple unranked players can still require manual attention. Browser-native codec support varies, and encoded black bars inside the original media are not removed.

Bulk file operations are serialized and use bounded copy buffers. They are not resumable background copy jobs; runtime errors can leave a partially completed batch, which the UI reports. Power loss can leave a hidden temporary copy. Permanent folder deletion has no recycle bin. Changes made directly on the SSD outside the app are not controlled by its locks.

## Package hygiene

The ZIP contains source, documentation, tests and checksums. It excludes the QA wrapper, authentication bypass, fixture media, populated databases, cookies, virtual environments, screenshots, compiled caches and logs. Verify extracted files with `sha256sum -c MANIFEST.sha256`.
