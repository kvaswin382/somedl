# Installation and network setup

[← README](../README.md) · [Setup](setup.md) · [Usage](usage.md) · [Maintenance](maintenance.md)

## First installation

Skip this section if Zedair is already installed; use the [upgrade instructions](../README.md#upgrade-an-existing-installation).

### 1. Get the source onto Ubuntu

Using GitHub:

```bash
sudo apt update
sudo apt install -y git
cd ~
git clone https://github.com/kvaswin382/somedl.git Zedair-Download-Desk
cd Zedair-Download-Desk
```

Or download the ZIP on Windows, then copy it using PowerShell:

```powershell
scp "$env:USERPROFILE\Downloads\Zedair-Download-Desk.zip" YOUR_UBUNTU_USERNAME@LAPTOP_IP:~/
```

On Ubuntu:

```bash
sudo apt update
sudo apt install -y unzip
cd ~
unzip Zedair-Download-Desk.zip
cd Zedair-Download-Desk
```

Find your Ubuntu username with `whoami` and the laptop IP with `hostname -I`. WinSCP/SFTP is also suitable for copying the ZIP. Extract into a clean folder so old files do not get mixed into a new release.

### 2. Connect the SSD and install

```bash
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS
sudo bash install.sh
```

1. Select your roughly 476.9 GiB exFAT SSD from the numbered list. Device names can change; check its size and filesystem.
2. Review the selected disk, then type `INSTALL`.
3. Wait for Ubuntu packages, Python packages and the Deno runtime to install. Internet access to Ubuntu mirrors, PyPI and GitHub is needed. Keep at least 2 GiB free on the eMMC.
4. Note the printed LAN URL and setup key.
5. Open `http://LAPTOP_IP:8787` from another device on your home network.
6. Enter the setup key and create your superuser with a password of at least 12 characters.

The installer never formats or repartitions a drive. It mounts the SSD by UUID at `/srv/zedair-ssd` and uses its `Zedair` folder. Files elsewhere on the SSD are not exposed by the app. exFAT stays compatible with Windows.

If the selected SSD is already mounted or has an existing conflicting fstab entry, the installer stops for you to review it. Do not blindly remove mount entries. Fix dependency/network errors and rerun the installer if the initial installation stops.

For speed testing, also follow **Test the laptop’s internet speed → One-time engine installation** in the linked guides. Other downloads do not require that optional engine.

### 3. Check the installation

```bash
systemctl status zedair zedair-transmission --no-pager
findmnt /srv/zedair-ssd
hostname -I
```

Both services normally show `active` with the SSD connected. Recover the first-run key with:

```bash
sudo cat /etc/zedair/setup-key
```

That key works only before an administrator exists. Browser logins last **35 days**. Sign out revokes that browser’s login; changing the administrator password signs out all devices. Platform cookies have separate expiry rules.

## Open the downloader at http://zed.local

After installing/upgrading, run **once on Ubuntu**:

```bash
sudo bash /opt/zedair/setup-local.sh
```

Then open **http://zed.local** on your phone/computer. No port is needed. Sign in once at the new hostname; browser login cookies for an IP address do not transfer to a different hostname. Your account, queue and saved platform sessions stay on the server. The old IP:8787 address remains usable.

This installs Avahi for multicast-DNS and a small systemd socket proxy from port 80 to the existing app port. It advertises `zed.local` without changing the Ubuntu machine hostname, and refuses an occupied port 80, a detected conflicting name, or an existing unmanaged proxy unit. It expects the app's normal HTTP configuration and refuses a TLS-enabled backend. It does not change the router or firewall and does not publish the service to the internet.

**Device support matters:** `.local` works when the client supports mDNS and the network allows multicast discovery. Phones on cellular data, guest networks, isolated Wi-Fi clients or a different VLAN may not reach it. It cannot be guaranteed on every device/router automatically. Test:

```bash
systemctl status avahi-daemon zedair-local.socket --no-pager
avahi-resolve-host-name -4 zed.local
curl -I http://zed.local
```

If the IP:8787 URL works but `zed.local` does not, check mDNS/client support and guest isolation. Reserve the laptop's IP in the router. For clients that cannot use mDNS, a router with local hostname/DNS support can provide a local address; configuration depends on that router. Do not enter a public DNS record for this private service.

If UFW is active, find your real LAN subnet using `ip -4 route`. Replace this **example** subnet with yours:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 80 proto tcp comment 'Zedair home LAN web'
sudo ufw allow from 192.168.1.0/24 to any port 5353 proto udp comment 'Zedair home LAN mDNS'
```

Do not enable UFW over SSH without preserving SSH access. No WAN port forwarding is needed. HTTP does not encrypt credentials; the HTTPS section explains the separate TLS option.

To undo only this optional address setup:

```bash
sudo systemctl disable --now zedair-local.socket
sudo systemctl stop zedair-local.service
sudo rm -f /etc/zedair/local-address-enabled
```

Your IP:8787 app still works. The original Avahi configuration is backed up as `/etc/avahi/avahi-daemon.conf.zedair-before`. If you have not made later Avahi changes, restore that file with `sudo cp /etc/avahi/avahi-daemon.conf.zedair-before /etc/avahi/avahi-daemon.conf` and `sudo systemctl restart avahi-daemon`. Do not blindly restore it over later intentional configuration. App rollback does not undo these optional system packages/units.

## Keep running with the lid closed

If Ubuntu sleeps when you close the lid:

```bash
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/zedair.conf >/dev/null <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
EOF
sudo reboot
```

Keep ventilation clear and the charger connected. A desktop environment is unnecessary. Existing zram/swap settings are preserved. Both services start automatically after boot when their dependencies are available.

## Network access and HTTPS

Reserve the laptop’s IP in your router’s DHCP settings so its URL stays stable. Phone/computer and laptop must be on the same LAN; guest Wi-Fi isolation can block access. Do not forward web ports **80 / 8787** or torrent RPC port **9092** to the internet.

The default webpage uses HTTP. For a trusted home LAN this keeps setup simple; HTTP does not encrypt credentials. If you need encrypted access, supply a certificate/key trusted by your devices, with the laptop’s hostname/IP in its SAN. Store them in `/etc/zedair`, readable by the zedair group, and add `tls_cert` and `tls_key` to the existing JSON using `sudoedit /etc/zedair/config.json`:

```json
"tls_cert": "/etc/zedair/lan-cert.pem",
"tls_key": "/etc/zedair/lan-key.pem"
```

Preserve valid JSON, including commas. Restart `sudo systemctl restart zedair` and use `https://LAPTOP_IP:8787`. The app then sets its login cookie’s Secure flag. Trust your local CA on each device instead of bypassing browser warnings. Certificate issuance is not automated by this package.

If UFW blocks access, find your actual subnet with `ip -4 route`. For **192.168.1.0/24 only as an example**:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8787 proto tcp comment 'Zedair home LAN'
```

Replace that subnet with yours. Do not enable UFW over SSH without preserving your SSH access. The installer makes no firewall changes.
