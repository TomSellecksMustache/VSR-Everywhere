# Changelog

## 1.4.1

- Check for stable GitHub releases and install compatible plugin updates from the settings page. Keep settings and activate the update after restarting Jellyfin.
- Verify update checksums and runtime compatibility; link runtime changes to the full installer.

## 1.4.0

- Use NVIDIA VSR as the sole upscaler, without an extra preprocessing stage or colour adjustment.
- Simplify feature controls and installation dependencies.
- Keep source frame rate, adaptive interpolation, automatic cropping, startup handoff, and RAM caching.
- Show matched Original / VSR comparisons throughout the current documentation.

## 1.3.1

- Include decoding and cropping time in the adaptive enhancement budget so larger inputs keep enough processing headroom.
- Account for interpolation cost separately and avoid increasing frame generation when enhancing each original frame is faster.

## 1.3.0

- Crop stable encoded black bars from the enhanced stream. Keep one crop through startup and seeks, with a full-picture fallback when detection is uncertain.
- Fit stream output to the picture's aspect ratio without adding a black canvas. Report the actual output size and HEVC format to the player.
- Keep existing VSR and interpolation defaults. The crop change supports 8-bit and 10-bit SDR input.

## 1.2.0

- Accept 8-bit and 10-bit SDR sources up to 1080p. HDR sources keep normal Jellyfin playback.
- Put VSR, Interpolation, and resolution controls below the device list. Other settings are under Advanced.
- Allow VSR to be switched off independently of interpolation.
- Build against the Jellyfin 10.10 API and .NET 8, with compatibility checks against 10.10, 10.11, and 12.1 APIs. Setup verifies plugin loading instead of requiring exactly 12.1.
- Remove the fixed 25 GB installation gate and explain actual disk-full errors.

## 1.1.0

- Added adaptive interpolation while keeping the input frame rate. There are no fixed 30/60/120 fps modes.
- Added a lighter startup pass and a continuous change to enhanced playback, with a small segment reserve to reduce early buffering.
- Improved pacing, seek handling, and encoder buffering. Applied known client bitrate limits.
- Added experimental Auto HDR, off by default. Reliable player HDR activation is still unresolved.
- Updated the Windows wizard, Linux packages, documentation, and matched Original / VSR comparisons.

Interpolation and Auto HDR are Windows-only. Linux keeps plain VSR. Input remains 8-bit SDR up to 1080p. The Windows installer is unsigned.

## 1.0.0

- Windows wizard and PowerShell installation with runtime downloads, video checks, backups, and rollback.
- Linux installer for native systemd Jellyfin services.
- Independent processing and delivery resolutions, NVIDIA VSR controls, HEVC Main 10, and AAC.
- RAM segment caching, disk fallback, seek regeneration, and device selection.
- Frame-pacing repairs, reusable verified runtimes, and MIT licensing for project code.
