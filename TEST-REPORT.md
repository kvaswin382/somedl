# Verification report — Zedair 2.0.0

Revision date: 12 November 2026. Target: Ubuntu 24.04 x86_64, Python 3.12, 2 GB RAM and external exFAT SSD.

## Scope

Implemented independently from main `3a0dd2e954a3f37ecdbdb670363585686b28e7d2`, after reviewing the claude branch at `ed86b688d31c02641c492a6004807ebd3119f654`. No live account credentials were used, Telegram messages sent, or laptop services changed during development.

## Automated coverage

The complete unit suite includes the previous 101 tests plus new regressions for:

- Actual image bytes overriding MP4 filenames/settings; direct non-media file publication.
- Renewed URL resume with matching strong ETag/range/length; changed entities and weak validators restart; error HTML never becomes a downloaded file.
- Adaptive master selection, quality ceilings, ambiguous-player rejection and matching the playing rendition to higher JSON variants.
- Cross-origin credential stripping, private redirect rejection and canonical Reddit/Threads share resolution.
- Private scoped cookie persistence, concurrent merges and masked Telegram tokens.
- Instagram story/highlight targets, owner folders and platform enable switches.
- Concurrent worker cancellation, cache preservation, usage accounting and fixed update allowlists.
- Authentication/CSRF on new APIs, Retry All behavior and masked-token preservation/clearing.
- Existing installer upgrade/rollback fixtures preserve private state and environment.

Exact final test counts and CI status are recorded with the completed revision.

## Real-engine local integrations

The eight pre-existing integration scripts passed using actual yt-dlp/gallery-dl/FFmpeg and local HTTP fixtures. `integration_profiles_formats.py` now verifies selection of 4320p WebM plus audio ahead of 2160p MP4, and respects an explicit 1080p ceiling.

Two new integration scripts passed:

- `integration_automatic_engine.py`: the production orchestration follows a nested player, selects the highest labelled rendition, keeps title and exact bytes, carries browser cookies and Referer to a protected fixture CDN, and inspects a bounded HLS master. **Browser discovery is substituted in its handoff scenario.**
- `integration_instagram_stories.py`: actual gallery-dl child jobs follow two highlight collections, fetch exact image bytes, emit owner/completion metadata and skip archived IDs on retry. **Instagram responses are fixtures.**

`integration_browser_capture.py` separately launches actual sandboxed Chromium, clicks a player inside an iframe, matches JSON variants to playback and resolves a lengthless HLS master. It exits 77 when Chromium is unavailable, and fails instead when `ZEDAIR_REQUIRE_BROWSER=1` (CI). It does not weaken the Chromium sandbox. Its result must not be conflated with mocked browser handoff tests.

## Environment limits

This workspace could not install the matching Playwright headless shell; the download failed. The available system Chromium also failed to launch under workspace IPC restrictions. Consequently actual Chromium discovery has not passed locally. GitHub CI is configured to require the real browser fixture rather than silently skip it.

The supplied Threads share and canonical post were inaccessible to the research tool. No end-to-end success is claimed for that live post, Reddit mobile links, live Instagram stories/highlights, expiring bigcdn URLs or arbitrary public websites. Cookies, player structure and source availability can differ on the laptop.

No physical Atom/2 GB performance, exFAT unplug, live torrent swarm, Telegram delivery or systemd/polkit installation was exercised. Unit/fixture tests cover their application interfaces, not actual hardware deployment. Installed dependency interfaces used for testing: Python 3.12, yt-dlp 2026.10.26, gallery-dl 1.32.19 and Playwright 1.65.1.
