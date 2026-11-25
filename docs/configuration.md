# VSR Everywhere plugin settings

Open VSR Everywhere in Jellyfin's plugin settings. Settings apply to new playback sessions. Device controls come first, followed by the VSR and Interpolation switches, input resolution limit, and two resolution presets. Quality, encoding, cache, and runtime paths are under the collapsed Advanced settings section.

## Input resolutions

**Process inputs above 800p** is off by default, including for existing configurations. Unchecked, the plugin processes sources at 800p and below. Checked, it also accepts sources up to 1080p. The limit uses the source height before cropping, with the existing maximum width of 1920 pixels. Sources outside the limit use normal Jellyfin playback. The setting applies to new playback sessions.

## Processing and delivery resolution

Processing resolution is the size VSR works at. Stream resolution is the maximum size sent to the player. They can be different. Output fits the picture's aspect ratio inside that limit without adding a black canvas.

The stream automatically crops stable encoded black bars. It checks several points before playback and keeps one crop through the startup pass, enhancement, and seeks. Uncertain borders stay in the picture. Your source file stays unchanged. A player can still show borders when the picture and display have different shapes.

| Preset | Size |
| --- | --- |
| 1080p | 1920 × 1080 |
| 1440p | 2560 × 1440 |
| 4K | 3840 × 2160 |
| 5K | 5120 × 2880 |
| 6K | 5760 × 3240 |
| 8K | 7680 × 4320 |

For 5K processing and 4K playback, choose those two presets. A larger processing canvas uses more GPU time. Output above 4K also needs an encoder and player that support it.

## Enhancements

| Setting | What it does |
| --- | --- |
| VSR | Turns NVIDIA VSR on or off independently of the other features. |
| VSR quality and strength | Controls the upscaling profile and enhancement strength. Start with Streaming Medium and 0.75. |
| Interpolation | Adjusts how often enhancement runs and fills the frames between. Output keeps the source frame rate. Enabled by default on Windows. |
| Auto HDR | NVIDIA TrueHDR conversion for eligible SDR input and HDR10 players. Off by default. Confirmed with the regular Jellyfin Roku app. |

[How these features work and their limits](enhancements.md)

## Encoder

| Setting | Default | Notes |
| --- | --- | --- |
| Video bitrate | 20 Mbps | Range: 4–60 Mbps for ordinary delivery. Registered Roku clients use the tested 40 Mbps CBR compatibility profile. |
| NVENC preset | p4 | Higher presets need more encoding work. |
| Tune | Default | HQ is available. |
| Lookahead | 0 | Nonzero values use the FFmpeg encoding path and transfer enhanced frames through RAM. |
| CQ | 0 | Zero disables the explicit CQ control. |
| Spatial / temporal AQ | Off / off | Optional adaptive quantization. |
| HLS format | MPEG-TS in the installer | fMP4 is also available. The player must support the selected format. |

The Roku profile uses five-second HLS segments and the compatible FFmpeg encoding path. Its 40 Mbps CBR mode overrides CQ, even when a CQ value is saved. Other clients retain their configured bitrate limits and normal encoder selection.

If playback falls behind, reduce processing resolution or quality, use p4 and lookahead 0, and keep one session active.

## Cache and seeking

VRAM holds working frames. RAM holds compressed HLS segments. The plugin does not load an entire film into VRAM.

Memory caching is the default. A budget of 0 selects automatic sizing: the smaller of 7 GiB or 60% of available physical RAM. This is shared across sessions and includes uploads and active reads. It is not a limit on the whole process's memory use.

Older segments can be evicted. Seeking to an uncached section generates it on demand; recent cached sections can be reused. The worker keeps loaded models across seeks. Default seek lookahead is 90 seconds and catch-up is 20 seconds. Seeks can still require substantial buffering, especially with high processing resolutions or a fresh encoder run.

If memory cannot be reserved, a session can fall back to disk. Disk mode uses the configured cache folder, space limits, and idle cleanup. Start with one concurrent session.

## Updates

Use **Check for updates** at the bottom of this page to check the latest stable GitHub release. Preview releases and unbuilt commits are excluded.

The 1.5.0 release provides native installers. Open the release link and run the Windows setup or Linux installer to upgrade. Choose the existing runtime folder to retain compatible settings and reuse verified dependencies.

For releases that include a compatible plugin update package, **Install** can download and verify it through Jellyfin's updater. Otherwise the page links to the full installer. Update controls require a Jellyfin administrator.

## Devices and source support

The installer enables all devices on a new install. Turn that off and select registered devices to limit access. Existing saved selections are kept during upgrades where available.

The input path accepts 8-bit or 10-bit SDR sources within the selected input resolution limit. HDR input keeps normal Jellyfin playback. Higher-depth SDR samples are decoded through 16-bit RGB before enhancement. SDR streams use 8-bit HEVC Main; Auto HDR uses 10-bit HEVC Main 10 with BT.2020/PQ signaling. Players must support the delivered format, AAC audio, HLS, and the chosen resolution.

[Overview](../README.md) · [Troubleshooting](troubleshooting.md)
