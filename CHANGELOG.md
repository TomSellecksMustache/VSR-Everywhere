# Changelog

## Unreleased

- Automatic GPU detection with NVIDIA and Intel overrides in plugin settings.
- Intel OpenVINO SR-1033 enhancement and Quick Sync HEVC Main 10 encoding, sharing the existing RAM cache and frame scheduler.
- Hardware-specific runtime downloads, pinned public model/license downloads, and pre-installation GPU/video checks on Windows and Linux.
- Intel-only installations avoid CUDA/PyTorch. Existing backend/profile/device settings are preserved.
- Intel model and muxing have CPU/software reference coverage; Intel GPU playback remains unverified.

## VSR Everywhere 1.0.0

- Project code licensed under MIT, with the license included in release packages.
- Windows install wizard with Jellyfin discovery, selectable runtime/download drive, dependency downloads, GPU/video checks, and plugin-load verification.
- Matching standalone PowerShell/ZIP installation and manual plugin package.
- Independent 1080p, 1440p, 4K, 5K, 6K, and 8K processing/delivery dropdowns.
- NVIDIA VSR quality/strength controls, HEVC Main 10 NVENC output, and AAC audio.
- Direct GPU resize/conversion/encoding path, with a compatible FFmpeg path for lookahead settings.
- Shared, bounded RAM segment cache; automatic budgeting, disk fallback, and seek-on-demand regeneration.
- Fixed-rate frame scheduling preserves decoded presentation order for jittered timestamps and packed B-frames, with calibrated seek anchors.
- Version-only upgrades can reuse an existing runtime when its verified dependency manifest is unchanged apart from the product version.
- Device selection and preserved settings during installation.
- Backups and rollback if plugin deployment or startup verification fails.
- Setup log sharing compatible with the wizard reader, fresh-entry startup checks, and BOM-free Jellyfin manifest output.
- Illustrated user documentation and eight matched screenshot comparisons.

Current source support is 8-bit SDR at up to 1080p. Higher output presets depend on GPU/player capability. Playback with Jellyfin Roku 3.2.4 and MPEG-TS has been confirmed. The installer is unsigned; clean-PC installation remains unverified.
