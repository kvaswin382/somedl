# Verification report — Zedair 1.1.0

Date: 5 November 2026. Target: Ubuntu 24.04 x86_64, Python 3.12, exFAT USB SSD.

## Passed

- **37 automated tests** with `python3 -m unittest discover -s tests -v`.
- Real yt-dlp integration over loopback, downloading a generated MP4 with FFmpeg available. Verified exact media bytes, title/progress output, metadata-based filename selection, numbered publication and preservation of an existing file.
- Python compilation for application, adapters, platform settings, publisher, speed test and installation/upgrade scripts.
- JavaScript syntax (`node --check static/app.js`) and shell syntax for install/update/upgrade entrypoints.
- Desktop browser inspection of charcoal sidebar, settings submenu, Instagram session fields, X video/session controls, platform setting save and speed-test controls.
- Mobile settings inspection at a 375 CSS-pixel content viewport. A navigation overflow was corrected; measured body width equals viewport width (375 px).
- Actual browser-triggered speed worker launch and failure display. Workspace DNS could not resolve the external endpoint; the UI reported the failure without invented results.

## Automated coverage

Baseline coverage includes mount/UUID/free-space guards, confined paths and symlinks, atomic URL batches, duplicate unfinished jobs, restart recovery, salted password verification, first-run setup, 35-day sessions, CSRF/origin protection, ranged file responses, queue retries/pause/unplug handling, Threads exact-post fixture extraction, and Transmission RPC handshake/control protections.

New coverage includes:

- Platform routing (including both x.com and twitter.com), platform folders/defaults, and X engine arguments.
- Manual Instagram/X session conversion, expiry handling, private storage, redaction and absence of secrets from snapshots.
- Missing Instagram authentication stops before launching the engine; recognized auth, access and rate-limit errors are classified separately from temporary failures.
- Numbered title collisions preserve existing bytes; repeat publication does not duplicate files.
- Recovery after a completed rename but before its database commit, without launching a second download.
- File-deletion guards for changed files, symlinks and files managed by Transmission; successful deletion of a disposable test fixture.
- Existing legacy job folders survive restart; hidden staging rejects symlinks.
- New speed-test API authentication/CSRF enforcement and platform-setting validation.
- Speed-test endpoint selection, bounded 41 MiB payload, maximum 8 MiB upload buffer, and refusal to report HTTP failures as success, using deterministic transport fixtures.
- App upgrade success and failed-health-check rollback in temporary directories with mocked service commands; private state and the existing Python environment survive both paths.

## Not verified on the physical laptop

No connection to the user's laptop or GitHub repository was available. The package was not installed there and nothing was pushed to GitHub.

Actual exFAT power-loss behavior, mount/fstab/systemd installation, RAM/CPU usage on the Atom, torrent swarms, Deno-based YouTube challenges, Instagram/X account-specific access and real internet throughput require validation on that laptop. The upgrade tests simulate service control; they are not a real systemd upgrade. Live account cookies were neither provided nor requested.

Instagram authentication was a suspected cause of the reported failures, not a proven diagnosis. This release adds session entry, checks and clearer error handling; it cannot guarantee that a website will accept a session or offer original-resolution media. Threads remains explicitly experimental.

The speed test is a bounded single-connection HTTP estimate to Cloudflare, not Ookla or an ICMP test. The workspace network prevented a successful external throughput measurement.

## Package hygiene

The release contains source, documentation, tests and checksums. It excludes the temporary browser-QA wrapper, preview authentication bypass, test data, cookies, databases, virtual environments, screenshots and logs. The checksum manifest can be checked with `sha256sum -c MANIFEST.sha256` from the extracted package directory.
