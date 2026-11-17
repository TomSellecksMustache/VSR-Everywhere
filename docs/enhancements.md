# Interpolation and HDR

These optional features are included in the Windows build. Linux uses the existing VSR path.

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

Enhancement and interpolation tensors use GPU memory. Encoded HLS segments use the plugin's shared RAM cache, with disk fallback.

Lookahead above 0 uses FFmpeg encoding and copies enhanced P010 frames through RAM. Lookahead 0 can use the direct GPU encoder path.

[Settings](configuration.md) · [Comparisons](comparisons.md) · [Third-party notices](../THIRD_PARTY_NOTICES.md)
