# Zedair Download Desk

**v2.1.2** · Ubuntu 24.04 · Python 3.12 · Built for a 2 GB headless laptop + USB SSD

Queue webpage links from your phone or computer. Zedair finds and downloads the media on the laptop, even after you close the browser.

[Install](#first-installation) · [Upgrade](#upgrade-an-existing-installation) · [Use](#everyday-use) · [Telegram](#telegram) · [Troubleshoot](#troubleshooting) · [Detailed guides](#guides)

## Upgrade an existing installation

Run on the **Ubuntu laptop**, from the folder containing `upgrade.sh`. Replace the first path if your checkout is elsewhere. Run each command only after the previous one succeeds.

```bash
cd ~/Zedair-Download-Desk
git status --short
git fetch origin
git switch main
git pull --ff-only
sudo bash upgrade.sh
```

- If Git reports local edits or a diverged branch, preserve and review them first. Do not force-reset the checkout.
- The upgrader backs up app/state under `/var/backups/zedair`, installs into `/opt/zedair`, and checks startup. It restores the backup if its health check fails.
- SSD downloads, saved cookies, accounts and queue history are preserved. Engine packages are updated separately.
- Refresh with **Ctrl+F5**; the footer should show **v2.1.2**. Pulling source alone does not update the running app.

**This update:** readable white confirmation text, gray file icons, aligned dropdowns, and Telegram diagnostics/recovery. The original blue and neutral theme remains.

## First installation

Already installed? Use **Upgrade** above; do not rerun the initial installer.

```bash
sudo apt update
sudo apt install -y git
cd ~
git clone https://github.com/kvaswin382/somedl.git Zedair-Download-Desk
cd Zedair-Download-Desk
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS
sudo bash install.sh
```

1. Connect the SSD. Check its size/filesystem, select it, then confirm with `INSTALL`.
2. Open the printed `http://LAPTOP_IP:8787` URL from your home network.
3. Enter the printed setup key and create an administrator password of at least 12 characters.

The installer **does not format the SSD**. It mounts it by UUID at `/srv/zedair-ssd` and saves downloads inside `Zedair`. Keep at least 2 GiB free on the internal disk. If an existing mount conflicts, review it before continuing.

Recover the first-run key with `sudo cat /etc/zedair/setup-key`. It only works before an administrator exists.

### Optional components — install once

| Feature | Command on Ubuntu |
| --- | --- |
| Browser playback detection / Facebook collection | `sudo bash /opt/zedair/install-browser.sh` |
| Official Ookla speed test | `sudo bash /opt/zedair/install-speedtest.sh` |
| Local address `http://zed.local` | `sudo bash /opt/zedair/setup-local.sh` |

After installing a browser or speed-test engine, run `sudo systemctl restart zedair`. These installations survive application upgrades. Keep Chromium’s sandbox enabled. Speed testing requires accepting Ookla’s terms in the UI and can use hundreds of MB of data.

## Everyday use

| Task | Where / behavior |
| --- | --- |
| Download media | **Downloads → Add downloads**; one URL per line, up to 100. Submit the original webpage when possible. |
| Quality and format | Defaults to **Highest quality + MP4**. Format applies to video; photos retain their actual image type. |
| Choose a folder | **Browse** selects an exact destination. Leave empty for a platform/domain folder. Existing filenames receive numbered suffixes. |
| Stories / highlights | **Tools → Instagram stories & highlights**; username, profile, story or highlight URL. Requires a usable Instagram session. Default: `Instagram/<username>`. |
| Whole profiles / profile pictures | **Tools → Instagram / Facebook**. Facebook collection is experimental. Retry the same job to retain collection progress. |
| Retry / refresh | **Retry all failed**, or job **Details → Refresh source & resume**. Validated resumable files continue; changed sources restart safely. |
| Manage SSD files | **SSD files**: sort, list/grid, preview, move, copy, rename or delete. Deletion is permanent. |
| Torrents | **Torrents → Add torrent**; v1/hybrid magnets or `.torrent` files. Removing a torrent keeps its data. |
| Settings | Sliders and switches autosave. Concurrency is **1–20**, default **1**; browser-based downloads always run one at a time. |
| System / data usage | **Settings → System usage / Data usage**. Data usage is a partial payload estimate, not your ISP’s total. |

**Hold queue** pauses media work and torrents. Pause retains partial downloads; clearing history does not delete completed files. M3U8/HLS is remuxed to MP4; GIF animation is encoded to MP4. Other video conversion copies compatible codecs without reducing resolution. If codecs cannot fit MP4, choose **Keep source container**.

### Website sessions

Save private sessions under **Settings → Platforms & cookies**. For Instagram, copy `sessionid` from your browser’s cookie storage; optional `csrftoken` and `ds_user_id` fields are available. X uses `auth_token` + `ct0`; Facebook uses `c_user` + `xs`. Other platforms accept **Netscape cookies.txt** imports.

Cookies are credentials: never commit or share them. A stored session is not proof the website accepts it. Resolve login challenges in your own browser, renew cookies when necessary, then retry. See the [step-by-step cookie guide](https://github.com/kvaswin382/somedl/blob/main/docs/usage.md#settings-and-website-login-cookies).

No engine supports every website. DRM, unavailable content, login challenges, site changes and rate limits can prevent downloads. Threads and Facebook profile extraction remain experimental. A direct CDN URL provides only that file’s quality and may expire; the original webpage allows discovery and refresh.

## Telegram

1. Add your bot to the private group and allow it to send messages/photos.
2. In **Settings → General & transfers → Telegram connection & diagnostics**, enter the bot token and numeric group ID (usually `-100…`).
3. Click **Save & test group delivery**. It saves the visible connection values before testing, without enabling routine notifications.
4. Enable download errors/screenshots as needed. Screenshots may contain private page content.
5. For critical alerts, enter the admin’s numeric user ID and have that user start the bot privately. Configure thresholds under **System alerts**; allow group pinning if wanted.

Ordinary diagnostics go **only to the group**. CPU/RAM/disk/temperature/low-SSD alerts go to the group and admin. Delivery failures now show Telegram’s explanation without exposing the token. Confirmed group migrations are followed automatically; a rejected screenshot can fall back to text. A pin failure does not mean the alert failed to send.

## Maintenance commands

| Task | Command |
| --- | --- |
| Update download engines | `sudo bash /opt/zedair/update.sh` (or available Update buttons in Settings) |
| Service status | `systemctl status zedair zedair-transmission --no-pager` |
| App logs | `sudo journalctl -u zedair -n 80 --no-pager` |
| Check SSD / internal space | `findmnt /srv/zedair-ssd` and `df -h / /srv/zedair-ssd` |
| Restart app | `sudo systemctl restart zedair` |
| Roll back app/state | `sudo bash upgrade.sh --rollback /var/backups/zedair/REPLACE_WITH_TIMESTAMP` |

Rollback restores the selected queue/settings/cookies too; it does not restore SSD payloads or engine packages. Keep backups private. See [maintenance and recovery](https://github.com/kvaswin382/somedl/blob/main/docs/maintenance.md) for safe SSD removal, backup cleanup and password reset commands.

## Troubleshooting

| Problem | Next step |
| --- | --- |
| Old UI after pull | Run `sudo bash upgrade.sh`, then Ctrl+F5. |
| Telegram HTTP 400 | Save & test again; read the detailed reason. Check the ID, bot membership/permissions, or topic status as indicated. |
| Browser unavailable | Rerun `install-browser.sh`; inspect its sandbox check. Do not disable the sandbox. |
| Website download fails | Open job **Details**. Update engines; address the reported authentication, rate-limit, network or source error. |
| SSD offline / torrent stopped | Check USB power/cable and mount UUID. Mount the SSD, then start `zedair-transmission`. Never disable mount checks. |
| MP4 conversion fails | Select **Keep source container** if the original codecs cannot be copied into MP4. |
| Webpage unreachable | Check laptop IP, port 8787, service status and LAN isolation/firewall. |

Use a trusted LAN. Do not forward ports 80, 8787 or torrent RPC 9092 to the internet. HTTP does not encrypt credentials; [HTTPS and LAN firewall setup](https://github.com/kvaswin382/somedl/blob/main/docs/setup.md#network-access-and-https) is available in the setup guide.

## Guides

| Guide | Contents |
| --- | --- |
| [Setup](https://github.com/kvaswin382/somedl/blob/main/docs/setup.md) | ZIP installation, SSD mounting, `zed.local`, lid-close behavior, HTTPS and firewall commands |
| [Usage](https://github.com/kvaswin382/somedl/blob/main/docs/usage.md) | Quality, naming, queue recovery, profiles, cookies, file operations, torrents and speed tests |
| [Maintenance](https://github.com/kvaswin382/somedl/blob/main/docs/maintenance.md) | Rollback, SSD removal/reconnection, logs, DNS checks, cleanup, password recovery and startup |
| [Development](https://github.com/kvaswin382/somedl/blob/main/docs/development.md) | Engine architecture, source map, test commands and upstream references |
| [Test report](TEST-REPORT.md) | Verification performed and remaining live-service limitations |

**MIT license.** Third-party engines retain their own licenses. No Docker, Redis or Node server is required.
