# Settings

Open VSR Everywhere in Jellyfin's plugin settings. Settings apply to new playback sessions.

## Resolution

Processing resolution is the size VSR works at. Stream resolution is what the player receives. They can be different. The image keeps its aspect ratio, with padding where needed.

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
| VSR quality and strength | Controls the upscaling profile and enhancement strength. Start with Streaming Medium and 0.75. |
| Neural rendering | Adds Veyra NR before VSR on Windows. Enabled by default on Windows. |
| Content profile | Automatic uses title metadata: plain VSR for animation, NR+VSR for live action. Animation and Live action overrides force the selected NR profile when NR is enabled. |
| Interpolation | Adjusts how often enhancement runs and fills the frames between. Output keeps the source frame rate. Enabled by default on Windows. |
| Auto HDR | Experimental SDR-to-HDR conversion. Off by default; player HDR activation remains unreliable. |

[How these features work and their limits](enhancements.md)

## Encoder

| Setting | Default | Notes |
| --- | --- | --- |
| Video bitrate | 20 Mbps | Range: 4–60 Mbps. Known client limits may lower delivery bitrate. Short bursts can exceed the average. |
| NVENC preset | p4 | Higher presets need more encoding work. |
| Tune | Default | HQ is available. |
| Lookahead | 0 | Nonzero values use the FFmpeg encoding path and transfer enhanced frames through RAM. |
| CQ | 0 | Zero disables the explicit CQ control. |
| Spatial / temporal AQ | Off / off | Optional adaptive quantization. |
| HLS format | MPEG-TS in the installer | fMP4 is also available. The player must support the selected format. |

If playback falls behind, reduce processing resolution or quality, use p4 and lookahead 0, and keep one session active.

## Cache and seeking

VRAM holds working frames. RAM holds compressed HLS segments. The plugin does not load an entire film into VRAM.

Memory caching is the default. A budget of 0 selects automatic sizing: the smaller of 7 GiB or 60% of available physical RAM. This is shared across sessions and includes uploads and active reads. It is not a limit on the whole process's memory use.

Older segments can be evicted. Seeking to an uncached section generates it on demand; recent cached sections can be reused. The worker keeps loaded models across seeks. Default seek lookahead is 90 seconds and catch-up is 20 seconds.

If memory cannot be reserved, a session can fall back to disk. Disk mode uses the configured cache folder, space limits, and idle cleanup. Start with one concurrent session.

## Devices and source support

The installer enables all devices on a new install. Turn that off and select registered devices to limit access. Existing saved selections are kept during upgrades where available.

The input path accepts 8-bit SDR sources up to 1080p. Other sources use normal Jellyfin playback. HEVC Main 10 output does not mean native 10-bit or HDR input is supported. Players must accept HEVC Main 10, AAC, the selected HLS format, and the delivery resolution.

[Overview](../README.md) · [Troubleshooting](troubleshooting.md)
