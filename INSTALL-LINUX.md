# Linux server setup

This installer is for a native Jellyfin systemd service. It installs plain NVIDIA VSR, with the same resolution controls, RAM cache, and seek handling as the Windows build. Veyra neural rendering, interpolation, and Auto HDR are Windows-only.

## Requirements

- A running, initialized Jellyfin systemd service under a non-root account.
- x86-64 Linux with glibc 2.28 or newer. The package targets compatible Debian/Ubuntu and RPM-based systems.
- An NVIDIA GPU with VSR and HEVC Main 10 support. Streaming quality modes need Ampere or newer.
- A supported NVIDIA driver: 570.190+, 580.82+, 590.44+, or a newer branch.
- Jellyfin FFmpeg/FFprobe, internet access, and room for the private runtime.
- `bash`, `curl` or `wget`, `tar`, `base64`, `sha256sum`, `realpath`, `stat`, and `flock`.

ARM, Alpine/musl, Docker, and non-systemd installations are outside this package. Automated checks run on Ubuntu 24.04 and Rocky Linux 9. Native Linux GPU playback still needs hardware validation; this is not a claim of support for every distribution.

The plugin targets the Jellyfin 10.10 API and .NET 8. Setup checks actual plugin loading instead of requiring one server version. API checks cover 10.10, 10.11, and 12.1, but this does not guarantee every server build. There is no fixed disk-space gate; an actual space failure explains how to retry.

## Install

Download [Install-VSREverywhere-1.3.1-Linux.sh](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.3.1/Install-VSREverywhere-1.3.1-Linux.sh), then run:

```bash
sudo bash ./Install-VSREverywhere-1.3.1-Linux.sh
```

Accept the detected service settings or supply your own. To put the runtime on another mount:

```bash
sudo bash ./Install-VSREverywhere-1.3.1-Linux.sh --install-dir /srv/vsr-everywhere
```

Use a root-owned path whose parents are not writable by other users. Downloads, runtime files, logs, and backups go there. The script includes the plugin and downloads a private Python runtime and pinned GPU libraries, checking their hashes. System Python is not used for GPU packages.

Fresh installs use 4K processing/delivery, HIGH quality, strength 0.75, 20 Mbps, p4, lookahead 0, MPEG-TS/AAC, RAM caching, and one session. HIGH supports Turing hardware too. Existing settings are kept where compatible. A verified existing runtime can be reused without downloading the same runtime again.

## Options

- `--yes`: unattended setup with the same checks.
- `--service`: choose a custom systemd service.
- `--data-dir`, `--config-dir`, `--log-dir`: override Jellyfin paths.
- `--ffmpeg`, `--ffprobe`: override encoder tools.
- `--check --yes`: check the platform, service, GPU access, configuration, and space without deploying the plugin. The bootstrap may still download private Python.

The installer checks active service arguments and environment before standard paths. It does not install Jellyfin, change the graphics driver, or disable SELinux/AppArmor.

## Tests, backups, and recovery

Before deployment, setup tests VSR, HEVC Main 10, AAC, and HLS as the Jellyfin service account. It checks both the direct GPU encoder and the FFmpeg path used for lookahead.

It then stops Jellyfin, backs up the plugin and XML settings, installs the files, restarts the service, and checks for a fresh plugin-loaded message. A deployment failure attempts to restore the backup and restart Jellyfin. A power loss or forced process kill may need manual recovery.

Logs are under `<install-dir>/jobs/setup-*/setup.log`. Backups are under `<install-dir>/backups/`, with a `restore.json` record. To restore manually, stop Jellyfin and restore only the recorded plugin directories and XML, preserving ownership. Then restart the service.

For GPU failures, check the service account's device access and the setup log. For SELinux/AppArmor failures, check audit logs and allow the chosen executable paths through an appropriate policy.

[Settings](docs/configuration.md) · [Building](docs/development.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)
