# Verification report — Zedair 1.4.0

Date: 8 November 2026. Target: Ubuntu 24.04 x86_64, Python 3.12, exFAT USB SSD.

## Passed

- **85 automated tests** with `python3 -m unittest discover -s tests -v`.
- Real gallery-dl cookie serialization regression: its writer exports session expiry as `0`; the old MozillaCookieJar reader interprets that as epoch zero. The new reader correctly recognizes it as a session cookie. Three simulated server rotations survive real gallery-dl write/reload cycles. This test makes no Instagram requests and uses no real account.
- Two consecutive queue jobs with a controlled downloader process exercise private working cookies, successful-session publication and next-job reuse.
- Real yt-dlp loopback integration downloads a generated MP4 with FFmpeg available. Verified exact media bytes, title/progress output, metadata naming, numbered publication and preservation of an existing file.
- Official Ookla package downloaded from the publisher's Packagecloud repository, verified against the pinned SHA-256, extracted and checked using `--version` and `--help`. Version: 1.2.0.84 x86_64-linux-musl. The official bundled manual documents machine-readable bandwidth as bytes/second. No live test was run and no agreement was accepted.
- Python compilation, JavaScript syntax and individual shell syntax checks for install/update/upgrade/speed-test installer entrypoints.
- Desktop browser checks: image preview loaded its actual pixels; MP4 metadata loaded and native video playback advanced; closing the dialog removed the media element. The separate Download action remains available.
- Dark theme and mobile speed-test layout checked at a 375 CSS-pixel content viewport: body width equals viewport width. Main background is RGB(21,21,23); speed card is RGB(32,32,35). Historical report details opened successfully.
- Browser speed values were explicitly labelled test fixtures, not network measurements. No production account or authentication bypass is included.

## v1.4 verification

- The automated suite covers optional names/extensions, 100-link batches, explicit/root/default destinations, file copy/move/rename and collision handling, date preservation, stale-file and path guards, torrent ownership, empty-folder rules, profile-picture selection, image-only configuration, long rate-limit cooldowns, invalid feed responses, media selection, authenticated preview neighbors across pagination, and existing regression coverage.
- Real gallery-dl processed a controlled 300-post feed across 30 pages and downloaded **330 exact-byte images**, including all images in mixed carousels. No video or lower-resolution thumbnail request was made. A forced mid-page HTTP failure kept that page's checkpoint; the next attempt replayed it without duplicate media. Short work slices resumed through the final page, and a completed retry made no further requests.
- The earlier false-counter/archive regression passes through the new profile worker: old print-only behavior is reproduced, the same failed job is recovered, and actual files are published without duplicates.
- Real yt-dlp downloaded local MP4 bytes through the new fallback handoff, preserving the detected webpage title and Referer and removing temporary cookies. **Browser detection itself was substituted in this test.** Media-candidate rules and refusal of DRM/challenge fallback are checked separately.
- Desktop UI checks confirmed optional images-only job submission, 100-entry preference, renaming a fixture without an extension (the `.png` extension remained), and image → video → image preview with arrow keys/Next and correct boundary buttons.
- The browser session reset during final QA. Remaining v1.4 mobile-form and real Chromium detector checks were not completed; prior 320-pixel layout checks belong to v1.3/v1.3.1. Do not interpret those earlier checks as fresh v1.4 validation.
- No live Instagram profile, adult video page or physical Zedair laptop was used. The compatibility feed, actual player detection, Ubuntu AppArmor setup and memory behavior still require target-machine validation. Neither universal website support nor complete access to every Instagram post is claimed.

## v1.3.1 regression and sorting verification

- The real gallery-dl 1.32.19 CLI reproduced v1.3's bug: `--print` emitted four completed-item messages, made zero media HTTP requests, created zero media files, and wrote four false archive entries.
- The same failed job was placed in its old `publish` phase with a false count, then reopened through the v1.3.1 startup migration. Its phase/count were repaired, the corrected `--Print` command fetched four exact-byte fixture images across two pages, the normal server worker published them, and the job finished successfully with a count of four.
- Repeating that job's download phase used the real replacement archive and made zero additional media requests, created no duplicate outputs and retained the count of four. All media came from a loopback HTTP server; only Instagram extraction responses were substituted. No live Instagram requests were made.
- Startup migration is one-time, retains old archive bytes and does not reset direct post jobs. Real published files are excluded from the empty-job repair.
- File HTTP tests cover all six sort orders, newest-first default, folder priority, hidden/symlink exclusion, validation/authentication, ordering before pagination, stable ties, stored download dates and modification-time fallback for an externally replaced file.
- Browser checks confirmed newest-first default, alphabetical ordering, largest-first ordering in list and grid, and oldest-first ordering on mobile. The sort selection persisted across page navigation; at 320 CSS pixels the file page had equal body/client scroll widths and the sort control wrapped below the view buttons.
- Publication records completion time without changing original file modification time. Clearing job history retains recorded dates; deleting a file removes its date record.
- The three earlier real-engine integration scripts also passed, alongside Python/JavaScript syntax checks. The previous profile test checked pagination/archive primitives but missed the CLI print flag; the new test exercises the CLI parser and actual media writes through the worker.

## v1.3 additions verified

- Direct Facebook/fb.watch and Reddit/redd.it routing, private Facebook cookie fields and default destination folders.
- Profile API authorization, username/URL validation, duplicate unfinished jobs, fixed platform/username paths, Instagram Posts/Reels/Everything configuration, unlimited profile post count and required optional Facebook dependency.
- Partial profile publication preserves completed files. Empty failed collections retain their original authentication error. Reaching Facebook's scroll limit requires attention; a video rate-limit response stops before the next video is requested.
- Facebook canonical link scoping and exact-photo-ID/highest-image selection with allowed CDN hosts. These are fixtures, not live Facebook downloads.
- Real gallery-dl 1.32.19 traverses three controlled pagination responses, filters reels from the posts feed and preserves its actual SQLite media-ID archive across reopening. No Instagram requests are made.
- Real yt-dlp 2026.10.26 format selection chooses the 2160p MP4 over a lower MP4 and higher WebM when MP4-only is selected; an explicit 1080p ceiling picks the lower matching MP4, and a WebM-only set has no match. The real loopback media download and existing cookie-serialization integration checks also pass.
- MP4 preference validation, per-job persistence and platform defaults. Merge/remux options are present; re-encoding is absent. Audio-only remains independent.
- CPU tick-delta calculation (including guest-counter handling), RAM availability, swap, missing sensors, zero swap and shared sample caching. `/api/system` requires login.
- Avahi configuration updates preserve unrelated settings and are idempotent; systemd proxy unit generation is checked. Root installation and LAN name resolution were not executed here.
- Desktop browser QA: Tools form, queued Instagram fixture and Facebook controls; real 600×400 image loaded in Grid while the file list contained zero video elements; MP4-only submission was persisted and displayed as “Highest available · MP4 only” in job details.
- Browser QA at 320 CSS-pixel content width: Tools, Settings/System usage, Files and queued downloads had body scroll width equal to viewport width. Gauges wrap into two columns and mobile navigation into two rows. Charcoal dark mode and light mode were inspected.
- Live system gauges displayed this development server's `/proc` readings, not measurements of the user's laptop. Speed history and sample downloads remained explicitly isolated test fixtures. No QA wrapper or authentication bypass is shipped.

## Automated coverage

Baseline tests cover storage UUID/mount/free-space guards, confined paths and symlinks, URL batches, duplicate unfinished jobs, restart recovery, password verification, first-run setup, 35-day sessions, CSRF/origin protection, queue control/retry/unplug handling, Threads exact-post fixtures, Transmission RPC protections, platform routing/settings, private cookie fields, title collisions, publication recovery and safe individual-file deletion.

New v1.2 coverage includes:

- Session cookies with zero/blank expiry remain usable on every read, including existing gallery-dl exports; positive past expiries are not extended.
- Session/CSRF rotation persists between jobs; invalid or empty exported jars cannot overwrite the saved session. GUI replacement/deletion wins over an older active download.
- Cookie working files have private permissions, filter non-Instagram domains at publication and participate in secret redaction. Completed/failed working files are cleaned up.
- Authentication, challenge and rate-limit messages remain distinct.
- Official speed conversion: 25,000,000 bytes/second becomes 200 Mbps. Invalid, nonfinite, negative, boolean or incomplete metrics are rejected.
- Deterministic subprocess fixtures verify live progress does not become a completed report, failure never reports success, cancellation/concurrency guards, required first-use acceptance and missing-engine handling.
- Legacy reports are labelled separately; CSV export guards spreadsheet formula prefixes.
- Authenticated HTTP preview responses: MIME allowlist, inline versus attachment, full/open/suffix byte ranges, HEAD, invalid ranges (416), unsupported inline formats (415), traversal and symlink rejection.
- Authenticated history export and folder preview metadata.
- App upgrade success and failed-health-check rollback in temporary directories with mocked service commands; private state and the existing Python environment survive both paths.

## Limits

No connection to the physical laptop, live Facebook/Instagram account or GitHub repository was available. This package was not installed there and nothing was pushed to GitHub. Actual exFAT power-loss behavior, mount/fstab/systemd/Avahi installation, cross-device zed.local resolution, Chromium sandbox launch, Atom CPU/RAM usage, torrent swarms, YouTube challenges and account-specific Instagram/Facebook/Reddit/X access require validation there. Upgrade tests simulate service control.

The Instagram expiry-zero bug is reproduced and fixed. That does not prove it was the only cause of the user's failures. Session refresh preserves cookies supplied during successful normal requests; it does not force Instagram to renew a login, bypass a challenge or guarantee a session lifetime. Threads and Facebook profile collection remain experimental. Facebook scroll exhaustion is not proof of a complete archive. Its live page structure, session acceptance and memory consumption require validation on the target laptop. MP4-only is best quality among matching MP4/M4A sources and cannot manufacture an unavailable format.

Speed testing now uses the official Ookla engine, but actual internet throughput and the precise reason for prior inflated readings were not measured on the laptop. The engine remains subject to the app service's resource limits. Browser-native codec support varies; there is no server transcoding.

## Package hygiene

The release contains source, documentation, tests and checksums. It excludes the browser-QA wrapper, authentication bypass, fixture media, populated databases, real cookies, downloaded Ookla binary, virtual environments, screenshots and logs. Check extracted contents with `sha256sum -c MANIFEST.sha256`.
