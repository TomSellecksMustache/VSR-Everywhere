# Settings and playback behaviour

[Overview](../README.md) · [Documentation](README.md) · [Windows setup](../INSTALL-WINDOWS.md)

Open **VSR Everywhere** in Jellyfin's plugin settings. Changes apply to new playback sessions; stop and restart playback when comparing profiles.

## Processing and delivery resolution

The processing preset sets the VSR processing canvas. The stream preset sets the encoded video delivered to the player. Aspect-ratio handling fits the active image to the selected canvas and adds padding where needed.

| Preset | Canvas |
| --- | --- |
| 1080p | 1920 × 1080 |
| 1440p | 2560 × 1440 |
| 4K | 3840 × 2160 |
| 5K | 5120 × 2880 |
| 6K | 5760 × 3240 |
| 8K | 7680 × 4320 |

For **5K processing with 4K delivery**, select 5K in the processing dropdown and 4K in the stream dropdown. Your player receives a 4K stream. The larger processing canvas increases work on the server; it is not a promise of better image quality for every source.

Choose delivery settings the player can decode. 6K/8K output also depends on the GPU encoder's limits. Preset availability does not establish real-time performance at that resolution.

## Enhancement and encoding

| Setting | Starting point | What it changes |
| --- | --- | --- |
| VSR quality | Streaming Medium | Enhancement model/profile. Use the examples to judge stronger modes. |
| Strength | 0.75 | Enhancement blend; higher values can make artifacts or texture changes more apparent. |
| Video bitrate | 20 Mbps | Encoded stream's target bitrate. The configurable range is 4–60 Mbps. |
| NVENC preset | p4 | Encoding work; p5–p7 can increase GPU load. |
| Tune | Default | Optional HQ tuning. |
| Lookahead | 0 | Keeps the direct GPU encoder path available. Values above 0 select compatible FFmpeg encoding. |
| CQ | 0 | Optional quality control; 0 leaves the explicit CQ control disabled. |
| Spatial/temporal AQ | Off | Optional adaptive quantization controls. |
| HLS format | MPEG-TS | New wizard default; fMP4 is also available. Client support varies. |

The screenshots use ULTRA/strength 1 and stronger encoder settings. Those settings are an example profile, not a recommendation for every server. If playback cannot keep up, start with 4K processing, Streaming Medium, p4, and lookahead 0.

## What stays on the GPU

On the direct GPU path, decoded video is converted/uploaded, then enhanced frames remain on the NVIDIA GPU through resize, P010 conversion, and HEVC encoding. Encoded video is muxed into HLS with AAC audio.

With NVENC lookahead above 0, the worker uses its compatible FFmpeg encoding path. VSR and NVENC still use the GPU, but enhanced frames are copied through system memory. A worker log entry identifies this fallback and its reason.

## RAM segment caching and seeking

**VRAM holds working video frames; RAM holds completed, encoded HLS segments.** The memory cache does not preload the entire source video into VRAM or RAM.

- **Memory (automatic)** is the new-install cache mode. Leave the memory budget at **0** for automatic sizing.
- The automatic budget is the smaller of **7 GiB** or **60% of available physical RAM** when calculated. It is shared across sessions.
- Cache accounting includes uploads and responses that are still being read. Older segments can be evicted as the budget fills.
- A seek to an uncached or evicted section regenerates that section on demand. Recent cached sections can be served directly.
- If sufficient memory cannot be reserved, the session can fall back to disk. Disk mode uses the configured cache path and space/idle limits.

The cache budget is not a cap on all memory used by Jellyfin, Python, the SDK, and the encoder. Start with one concurrent session. Higher resolutions and more sessions need additional GPU and system resources.

The default seek lookahead is 90 seconds and the catch-up threshold is 20 seconds. The server supplies a seekable full-duration playlist while generating segments only as needed.

## Devices

The wizard enables VSR and defaults to **all devices** on a new installation. To restrict it, turn off the all-devices option and select registered Jellyfin devices. Existing saved selections are carried forward where available.

The selected player must accept the enhanced HEVC Main 10/AAC HLS media source and its delivery resolution. Playback with Jellyfin Roku 3.2.4 and MPEG-TS has been confirmed on the maintainer's setup.

## Source eligibility

The current VSR worker accepts **8-bit SDR video up to 1920 × 1080**. 4K originals, 10-bit sources, and HDR are outside this input path. No HDR tone mapping is performed by this plugin. HEVC Main 10 delivery describes the output format, not the supported input bit depth.

For sources the plugin rejects as ineligible, use Jellyfin's standard playback path. See [troubleshooting](troubleshooting.md) if an expected source is not enhanced.
