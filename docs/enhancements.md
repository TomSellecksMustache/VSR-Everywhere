# Neural rendering, interpolation, and HDR

These features are included in the Windows build. Linux uses the existing VSR path.

## Neural rendering

The optional NR stage uses [Veyra-NRVideo](https://github.com/Likely7/Veyra-NRVideo) before NVIDIA VSR. It uses Veyra's experimental community runtime, including its compatibility path for RTX 30/40 hardware. It is not NVIDIA's official DLSS 5 game integration.

Automatic mode reads the current title's Jellyfin metadata, including genres and tags. Animation uses plain VSR, which tends to preserve flat colors and outlines better. Live action uses NR+VSR when NR is enabled. Unknown titles use the conservative NR profile. There is no external title lookup or AI-service call.

The Animation and Live action overrides force their NR profile when NR is enabled. Add an Animation genre or tag in Jellyfin if automatic selection misses a title. Disabling NR leaves plain VSR.

NR returns sRGB pixels. The worker converts them back to BT.709 before VSR so shadows and colors stay consistent with the video path. This follows the [sRGB](https://www.w3.org/Graphics/Color/srgb) and [BT.709](https://registry.color.org/rgb-registry/bt709) transfer functions; it does not add a saturation or contrast filter.

## Interpolation

The delivered frame rate matches the input, including fractional rates such as 23.976. There are no fixed 30/60/120 fps modes.

When enhancement takes too long, the worker enhances an anchor every two to four source frames and interpolates the frames between. Faster processing can return to enhancing every frame. Shot-change detection avoids blending across cuts, and seeks reset the frame history.

This helps processing keep up, but can introduce motion artifacts. It cannot compensate for an encoder that is itself too slow. Disabling interpolation processes every source frame and may need lower quality or resolution to keep up.

## Startup

Playback starts with a lighter pass while enhancement models load. It normally changes to full enhancement after about eight seconds of video. The same encoder, audio track, resolution, and timestamps continue through the change.

The first segment waits for a small reserve of contiguous segments. This reduces the early pause after playback starts, but startup still needs some buffering. Short clips can end before enhancement takes over.

## Auto HDR

Auto HDR is experimental and off by default. It uses NVIDIA TrueHDR for eligible SDR input when the player advertises HEVC HDR10 support. Other players receive SDR. Existing HDR sources use normal Jellyfin playback.

The server can encode BT.2020/PQ video, but this does not reliably activate HDR on players yet. Leave Auto HDR off if you want normal SDR output. HEVC Main 10 alone does not mean HDR is active.

## Memory use

Veyra runs as a separate process and transfers frames through RAM pipes. It does not cache uncompressed video on disk. Enhancement and interpolation tensors use GPU memory. Encoded HLS segments use the plugin's shared RAM cache, with disk fallback.

Lookahead above 0 uses FFmpeg encoding and copies enhanced P010 frames through RAM. Lookahead 0 can use the direct GPU encoder path.

[Settings](configuration.md) · [Comparisons](comparisons.md) · [Third-party notices](../THIRD_PARTY_NOTICES.md)
