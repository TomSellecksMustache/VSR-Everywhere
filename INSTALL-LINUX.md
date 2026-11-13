# VSR Everywhere 1.0.0 - Linux server setup

The Linux installer targets native **systemd Jellyfin services** across Debian/Ubuntu and RPM-based distributions. It installs a private, pinned Python/CUDA/VFX runtime, verifies the GPU as the Jellyfin service account, preserves existing plugin settings, and backs up the plugin before restarting Jellyfin. It uses the same GPU pipeline, RAM cache, seek handling, and resolution/device controls as the Windows build.

## Requirements

- An initialized, running **Jellyfin 12.1.x** systemd service under a non-root account.
- **x86-64 Linux with glibc 2.28 or newer.** NVIDIA lists Ubuntu 20.04/22.04/24.04, Debian 12, and RHEL 8/9 for its Python runtime. Other compatible glibc distributions, including Fedora and Rocky, use the same installer but require local validation. ARM, musl/Alpine, Docker, and non-systemd services are outside this package.
- An NVIDIA GPU with VSR and HEVC Main 10 support. NVIDIA specifies Linux drivers **570.190+, 580.82+, or 590.44+**; a newer driver branch is also accepted. Streaming modes require Ampere or newer.
- Alternatively, an Intel GPU with OpenVINO GPU access and HEVC Main 10 Quick Sync. Install distribution-appropriate Intel compute/media drivers and grant the Jellyfin service access to its Intel render device. [GPU override and Intel validation status](docs/gpu-backends.md).
- The server's Jellyfin FFmpeg and FFprobe, internet access, and **25 GiB free** for a fresh runtime.
- `bash`, `curl` or `wget`, `tar`, `base64`, `sha256sum`, `realpath`, `stat`, and `flock`. These are normally supplied by the distribution's coreutils/util-linux packages.

NVIDIA's binary runtime sets the compatibility boundary: this cannot promise support for every Linux architecture or libc. See [NVIDIA's Python runtime requirements](https://pypi.org/project/nvidia-vfx/) and [Linux VSR driver requirements](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoSuperResolution.html).

## Install

Download [Install-VSREverywhere-1.0.0-Linux.sh](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.0.0/Install-VSREverywhere-1.0.0-Linux.sh) to the server, then run:

```bash
sudo bash ./Install-VSREverywhere-1.0.0-Linux.sh
```

Press Enter to accept the runtime location and detected service settings. The script contains the plugin and setup code; it downloads the larger dependencies over HTTPS and checks their hashes. It uses a private Python 3.12 runtime, so the system Python version does not determine which GPU wheels are installed.

Fresh Linux installs use 4K processing/delivery, HIGH quality, strength 0.75, 20 Mbps, p4, lookahead 0, MPEG-TS/AAC, automatic memory caching, and one session. HIGH also supports Turing GPUs; streaming modes require Ampere or later. Existing quality, resolution, encoder, and device selections are retained.

New installs select the GPU automatically; the plugin settings offer NVIDIA and Intel overrides. Setup installs only the runtimes needed by detected GPU vendors, including the public, hash-verified Intel model. Intel-only servers do not download CUDA/PyTorch. NVIDIA quality modes and NVENC controls apply only to NVIDIA.

To use another drive or mount:

```bash
sudo bash ./Install-VSREverywhere-1.0.0-Linux.sh --install-dir /srv/vsr-everywhere
```

Choose a root-owned path whose parent directories are not writable by other users. Downloads, private libraries, setup temporary files, logs, and backups use that location. Playback caching defaults to RAM, with disk fallback below the Jellyfin data folder. Existing verified runtimes are reused without requiring another 25 GiB free.

For unattended setup with the same checks:

```bash
sudo bash ./Install-VSREverywhere-1.0.0-Linux.sh --yes --install-dir /srv/vsr-everywhere
```

For a custom service or layout, use `--service jellyfin.service`, `--data-dir`, `--config-dir`, `--log-dir`, `--ffmpeg`, or `--ffprobe`. Active process arguments and environment variables are checked before the standard `/var/lib/jellyfin`, `/etc/jellyfin`, and FFmpeg locations.

`--check --yes` checks the platform, service, GPU access, driver, package, configuration, and free space without deploying the plugin or installing CUDA packages. The shell bootstrap may still download its small private Python runtime.

## Installation and recovery

VSR, HEVC Main 10, AAC, and HLS must pass synthetic-media tests **as the Jellyfin account**, using its environment, before deployment. NVIDIA also checks the FFmpeg/NVENC path for nonzero lookahead. Intel uses OpenVINO and Quick Sync. Setup then stops Jellyfin, backs up this plugin and its XML configuration, installs the files, restarts the service, and confirms a fresh plugin-loaded message in the journal or Jellyfin log. A deployment or startup failure restores the previous files and starts the service again. An interruption during deployment is also handled; a power loss or forced process kill can require manual recovery.

Logs remain in `<install-dir>/jobs/setup-*/setup.log`. Backups are in `<install-dir>/backups/`, with the original plugin directories, configuration XML, and `restore.json`. To recover manually, stop the service, restore only the recorded VSR Everywhere directories and XML from that backup, preserve their ownership, then start Jellyfin. Other plugins and media files are outside the deployment target.

If the GPU check fails, inspect device permissions for the Jellyfin account, the driver version, and the setup log. On SELinux/AppArmor systems, also check audit denials and the service's allowed executable paths. Apply a policy appropriate to the chosen runtime location; setup does not disable security enforcement.

The installer itself does not install Jellyfin or replace the graphics driver. Install/initialize those through your distribution's supported process first.

## Build and validation

With .NET 10 SDK and Python 3 available in the source tree:

```bash
python3 tools/build_linux.py
python3 -m unittest discover -s tests -p 'test_linux_*.py'
```

The build produces the standalone `.sh`, a `.tar.gz` containing the same installer and guides, and SHA-256 checksums in `dist/`. No Windows executable is required on the Linux server. The project code is [MIT licensed](LICENSE); dependencies retain their own licenses in [the notices](THIRD_PARTY_NOTICES.md).

Baseline installer, ownership, plugin/cache, and package checks passed on Ubuntu 24.04 and Rocky Linux 9. Updated automatic-GPU and Intel reference checks are included in CI but have not yet run on native Linux. Intel GPU inference/Quick Sync and native Linux GPU playback remain unverified.
