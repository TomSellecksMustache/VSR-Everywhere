# Changelog

## 1.1.0

- Added optional Veyra neural rendering on Windows.
- Automatic profiles use plain VSR for animation and NR+VSR for live action. Manual overrides remain available.
- Corrected NR color transfer so it does not lift shadows or wash out colors before VSR.
- Added adaptive interpolation while keeping the input frame rate. There are no fixed 30/60/120 fps modes.
- Added a lighter startup pass and a continuous change to enhanced playback, with a small segment reserve to reduce early buffering.
- Improved pacing, seek handling, and encoder buffering. Applied known client bitrate limits.
- Added experimental Auto HDR, off by default. Reliable player HDR activation is still unresolved.
- Updated the Windows wizard, Linux packages, documentation, and matched Original / VSR / NR+VSR comparisons.

Neural rendering, interpolation, and Auto HDR are Windows-only. Linux keeps plain VSR. Input remains 8-bit SDR up to 1080p. The Windows installer is unsigned.

## 1.0.0

- Windows wizard and PowerShell installation with runtime downloads, video checks, backups, and rollback.
- Linux installer for native systemd Jellyfin services.
- Independent processing and delivery resolutions, NVIDIA VSR controls, HEVC Main 10, and AAC.
- RAM segment caching, disk fallback, seek regeneration, and device selection.
- Frame-pacing repairs, reusable verified runtimes, and MIT licensing for project code.
