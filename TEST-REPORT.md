# Verification report

Package: Zedair Download Desk 1.0.0
Date: 3 November 2026
Target: Ubuntu 24.04 x86_64, Python 3.12, external exFAT SSD

## Passed

**25 automated tests**, run using `python3 -m unittest discover -s tests -v`:

- Folder traversal, absolute paths, Windows reserved filenames and symlink escapes are rejected.
- Missing mounts, incorrect/missing SSD identity and the free-space reserve block new work.
- Invalid batches are rejected without partially adding jobs; unfinished duplicates are skipped.
- Queue/settings persist across server recreation; interrupted running jobs are re-queued.
- Password hashing is salted, valid passwords work and invalid passwords fail.
- Cookie profiles are selected by source rather than indiscriminately shared between engines.
- A failed subprocess schedules a bounded retry with recorded error and attempt count.
- A successful subprocess persists its result and title.
- A simulated SSD disappearance stops the active subprocess and blocks its job.
- Video command arguments retain quality ceilings, resume behavior and SSD temporary paths.
- Threads fixture extraction selects the exact requested post, includes all exposed carousel items and picks the largest variants; unrelated post/profile thumbnails are rejected.
- Local/private URL targets and embedded credentials are rejected; source routing respects hostname boundaries.
- First-run setup requires the installation key, can happen only once, and creates a 35-day HttpOnly/SameSite session.
- Unauthenticated API access and missing CSRF/cross-origin mutations are rejected.
- Folder creation and ranged file downloads work through the real HTTP server.
- Cookie files are stored with private permissions and their values are not returned by the state endpoint.
- Changing the administrator password revokes sessions.
- Torrent file selections omit empty arrays (which would otherwise mean all files in Transmission).
- Torrent operations outside the configured download root are refused.
- Saved torrent settings survive a temporarily offline engine.
- Transmission's HTTP 409 session-ID handshake, HTTP authentication header and RPC envelope work against a local protocol simulator.

**Real media-engine integration:** yt-dlp 2026.10.26 downloaded an FFmpeg-generated one-second MP4 from a loopback fixture server. The output matched the source bytes exactly. Title and JSON progress output were verified. The initial test exposed suppressed progress from `--print`; explicitly enabling `--progress` fixed it.

**gallery-dl CLI:** version 1.32.19 accepted the generated command-line options and configuration-path argument. No Instagram account or live authenticated media was used.

**Browser QA:** inspected first-run layout; used an isolated authenticated test fixture to exercise settings save, folder creation, destination selection and adding a queued download. Reviewed desktop layout and a 390-pixel-wide mobile frame; measured no horizontal document overflow in that frame. Mobile Settings exposes theme and logout controls. Preview-only credentials/fixtures and preview launchers are excluded from the deliverable.

**Static checks:** Python compilation, JavaScript syntax and shell syntax passed.

## Not verified here

- The root installer, systemd unit execution, physical USB unplug/reconnect and exFAT permissions on the user's actual Ubuntu laptop. The installer is supplied for that first real run.
- Actual Transmission daemon behavior and a real peer-to-peer download. Its adapter/API contract was tested with a local protocol simulator; the testing environment could not install the daemon.
- Live YouTube/Instagram account sessions or every supported website. The tested direct-media transfer does not prove current access to a particular site's changing extraction endpoints.
- Live Threads access. Its exact-post parser is fixture-tested; public-page retrieval remains experimental and clearly labeled in the UI. It deliberately fails rather than downloading unrelated thumbnails if structured media is unavailable.
- RAM/CPU/power measurements on the Atom laptop, optional HTTPS provisioning, or every phone browser. Conservative concurrency and systemd limits are defaults, not a measured performance guarantee.

## Re-run locally

From the extracted package:

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js   # optional; Node is not required to run the desk
bash -n install.sh update.sh
```

For the real media-engine fixture after installation (uses the installed yt-dlp and FFmpeg):

```bash
/opt/zedair/venv/bin/python tests/integration_media.py
```

No tests require a personal cookie file. The loopback media test is separate from production URL validation and cannot enable local-network fetching in the application.
