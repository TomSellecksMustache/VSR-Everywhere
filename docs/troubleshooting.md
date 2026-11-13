# Troubleshooting and recovery

[Overview](../README.md) · [Documentation](README.md) · [Windows setup](../INSTALL-WINDOWS.md)

Start with the wizard's **Open setup log** link. It identifies the stage that failed. Setup tests the runtime before changing Jellyfin and attempts to restore the previous plugin/configuration if installation verification fails.

## Setup problems

| Symptom | What to check |
| --- | --- |
| Not enough room on C: | Select a local drive/folder with 25 GB free for downloads and the runtime. Choosing `G:\` creates `G:\VsrEverywhere`. Small bootstrap and Microsoft runtime files still use system-drive space. |
| Jellyfin was not detected | Initialize a Jellyfin 12.1 server first, or choose its data folder and `jellyfin.exe` manually. The data folder must contain `config\system.xml`. |
| NVIDIA hardware/driver check failed | Confirm the NVIDIA driver is installed and at least 570.65. The runtime test must also succeed for the actual GPU. |
| Download/checksum failed | Run the matching release again with the same runtime folder. Complete, verified downloads are reused. Keep the ZIP and PowerShell file from the same build together. |
| Microsoft runtime requests a reboot | Restart Windows as requested by that installer, then rerun setup. |
| Windows displays an unfamiliar-app prompt | The current EXE is unsigned. Check that the file is the intended release and retain Windows' normal security controls. |
| VSR test failed or effect was not initialized | Read the underlying NVIDIA/worker error in the setup log. Check available VRAM, driver/runtime compatibility, and the selected processing size. Retry with 4K processing and Streaming Medium before increasing quality. |
| Plugin loading was not confirmed | Inspect Jellyfin's startup log for the current startup and VSR Everywhere 1.0.0. Use the current installer and retain the setup log and backup record. |

The current package writes its Jellyfin manifest as UTF-8 without a byte-order mark, shares setup logs with the wizard reader, and verifies fresh Jellyfin log entries by their recorded timestamps. A repeated failure should be investigated using its current log, rather than assuming those earlier packaging problems are still the cause.

## Playback problems

| Symptom | What to check |
| --- | --- |
| Video plays without VSR | Check the plugin/device settings and source eligibility: 8-bit SDR, at most 1080p. Start a new playback session after changing settings. |
| Stuttering or slower-than-real-time processing | Reduce processing size/quality, use p4, set lookahead to 0, and keep one session active. Other GPU applications can reduce available resources. |
| Unexpected system-memory traffic | Lookahead above 0 uses compatible FFmpeg encoding with frame transfers through RAM. The direct GPU path requires lookahead 0. |
| Disk cache files appear in memory mode | The session may have fallen back because sufficient memory could not be reserved. Check the Jellyfin log's cache-mode message and available RAM. |
| Picture looks too sharpened or artificial | Reduce enhancement strength or try another quality profile. Compare texture and compression artifacts, not only edge sharpness. |
| HEVC/8K/HLS playback fails on a client | Select a delivery resolution and HLS format the client supports. Jellyfin Roku 3.2.4 has been checked with MPEG-TS; other combinations need client testing. |

## Where files are stored

| Item | Location |
| --- | --- |
| Private runtime and downloads | `<selected folder>\VsrEverywhere\` |
| Setup jobs/logs | `<selected folder>\VsrEverywhere\jobs\<job id>\` |
| Small extracted bootstrap payload | `%ProgramData%\VsrEverywhere\setup\` |
| Plugin | `<Jellyfin data>\plugins\VSR-Everywhere\` |
| Configuration | `<Jellyfin data>\plugins\configurations\Jellyfin.Plugin.VsrEverywhere.xml` |
| Backups | `<Jellyfin data>\vsr-everywhere-backups\` |
| Disk fallback cache | `<Jellyfin data>\cache\vsr-everywhere\` |

## Recovery or removal

For a failed setup, retain the backup directory and its `restore.json` record. It identifies the original plugin and configuration paths. Stop the selected Jellyfin server before restoring files; keep failed-install folders outside Jellyfin's `plugins` directory to avoid loading duplicate plugin copies.

To remove VSR Everywhere manually, stop Jellyfin, move `plugins\VSR-Everywhere` outside the plugins directory, and restart the server. Retain the configuration and backup if you may reinstall. The private runtime/download directory is separate from the plugin and should only be removed once it is no longer in use.

## Useful details for a problem report

Include the release version, Windows/Jellyfin/driver versions, GPU model, source dimensions/bit depth, selected profile, client, and the relevant setup or Jellyfin log section. `runtime-test.json` records the wizard's video-test result when available. Avoid uploading entire media libraries or unrelated logs.
