# Troubleshooting and removal

Start with **Open setup log** in the wizard. Setup tests processing before changing Jellyfin and attempts to restore the previous plugin if deployment fails.

## Installation

| Problem | Check |
| --- | --- |
| Not enough disk space | Select another local folder with 25 GB free. The small bootstrap and Microsoft runtime still need some system-drive space. |
| Jellyfin not found | Initialize Jellyfin 12.1 first, or choose its data folder and executable. The data folder must contain `config/system.xml`. |
| Download or checksum failure | Use files from the same release and rerun with the same runtime folder. Complete, verified downloads can be reused. |
| Microsoft runtime asks for a reboot | Restart as requested, then rerun setup. |
| Unfamiliar-app warning | The EXE is unsigned. Check that the file came from the intended release. |
| VSR effect not initialized | Read the underlying worker error. Check the NVIDIA driver, free VRAM, runtime compatibility, and processing resolution. |
| Plugin load not confirmed | Check Jellyfin's fresh startup log for VSR Everywhere 1.1.0 and any loading error. Retain the setup log and backup record. |

## Playback

| Problem | Check |
| --- | --- |
| No enhancement | Check the enabled device, source eligibility, and plugin settings. Stop and restart playback after changing settings. |
| Buffering or uneven motion | Lower processing resolution/quality, use p4 and lookahead 0, and keep one session active. Check other GPU workloads. |
| Strange textures or outlines | Reduce strength, disable NR, or try another quality profile. Automatic mode uses plain VSR for animation. |
| Motion artifacts | Try disabling interpolation. Processing every frame may need lower quality to keep up. |
| HDR does not activate | Auto HDR is experimental and off by default. Server HDR metadata does not guarantee player HDR activation. Leave it off for SDR. |
| HEVC or HLS playback fails | Check the player's HEVC Main 10, AAC, HLS format, and resolution support. Try 4K or lower delivery. |
| RAM traffic with GPU processing | Veyra uses RAM pipes. Nonzero lookahead also transfers frames through RAM for FFmpeg encoding. |
| Disk cache in memory mode | A session can fall back to disk when RAM cannot be reserved. Check the log and available memory. |

Only 8-bit SDR input up to 1080p enters this processing path. Other sources use normal Jellyfin playback. [Settings and limits](configuration.md).

## Recovery and removal

Windows backups are in `<Jellyfin data>/vsr-everywhere-backups/`. Linux backups are under `<install-dir>/backups/`. Keep the backup's `restore.json`, which records the original plugin and settings paths.

To restore manually, stop Jellyfin, restore only the recorded VSR Everywhere files, and restart it. Keep failed plugin copies outside `plugins` so Jellyfin cannot load duplicates. Preserve file ownership on Linux.

To remove the plugin, stop Jellyfin, move its `plugins/VSR-Everywhere` folder outside `plugins`, then restart Jellyfin. Keep settings and backups if you may reinstall. Remove the separate runtime folder only after nothing uses it.

For a bug report, include the release, OS/Jellyfin/driver versions, GPU, source format, settings, player, and relevant log section. Remove tokens and unrelated personal data before posting logs.

[Windows file locations](../INSTALL-WINDOWS.md) · [Linux setup](../INSTALL-LINUX.md) · [Overview](../README.md)
