# Development and engine reference

[← README](../README.md) · [Setup](setup.md) · [Usage](usage.md) · [Maintenance](maintenance.md)

## Download engine

1. Resolve supported Reddit mobile-share and Threads share links to canonical post URLs, with bounded redirects restricted to that platform.
2. Inspect actual response bytes and content type. Direct images and files use a bounded streaming HTTP downloader. **Video format settings never rename photos to video extensions.** A JPEG stays `.jpg`, even if MP4 and a custom filename were selected. GIF animation conversion is a separate explicit behavior.
3. Try maintained site extractors: yt-dlp and supported gallery-dl extractors, including image hosts. Threads also reads structured data for the exact requested post and selects its largest exposed rendition, without substituting recommendations or preview thumbnails.
4. Parse static player sources, video metadata and nested iframe pages. Explicit download links and supported image-host pages can also lead to file downloads.
5. When needed, launch one sandboxed Chromium instance. Observe network responses, rendered players, shadow roots and bounded embedded frames; try visible play controls and collect variants before selection. Link a playing low-quality URL to higher variants advertised by its player data. Inspect bounded HLS/DASH manifests instead of assuming the first playing URL is the best quality. Close Chromium before the file transfer.

The default is **Highest quality + MP4**. The video extractor requests the best available streams without an MP4-only filter or hidden 1080p cap. An explicit resolution selects a ceiling; MP4 output copies compatible original codecs into MP4 without re-encoding. Unsupported codec/container combinations report an error instead of silently selecting a smaller rendition. A direct file URL offers only that file's quality; submit the original webpage to discover other renditions.

**Refresh source & resume** is available in a failed/paused individual download's Details. It opens the stored original webpage again; no replacement CDN URL needs to be pasted. Failed transfers can also re-extract automatically. Progressive HTTP downloads append only after a strong ETag, matching length and correct byte range confirm the same entity. A changed or unverifiable entity restarts safely. Browser-discovered adaptive manifests restart when their previous representation cannot be validated; site extractors retain their native resume behavior. Expired direct CDN links cannot reveal a missing original webpage.

Website cookie persistence is optional, private and domain-scoped. Browser requests and file transfers share the relevant cookie jar, Referer and browser identity. **No cookies** disables saved-session reuse for that job. Deleting a platform's cookies also removes its saved browser sessions and prevents in-flight jobs from restoring them. Cookies improve continuity where a site accepts them; they do not guarantee exemption from bot detection. Login/verification, rate limits and DRM are not bypassed.

No extractor can promise every website. Ambiguous unrelated players, protected content, changed site APIs and some browser-only session mechanisms can still fail. The supplied Threads share/post and expiring CDN examples have not been confirmed as working live in this environment. See [TEST-REPORT.md](../TEST-REPORT.md) for what was actually tested.

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
