# v2.1.3 ETA verification

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
