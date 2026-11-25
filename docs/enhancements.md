# NVIDIA VSR, adaptive interpolation, and Auto HDR

These optional features are included in the Windows build. Linux uses the existing VSR path.

## Interpolation

The delivered frame rate matches the input, including fractional rates such as 23.976. There are no fixed 30/60/120 fps modes.

When enhancement takes too long, the worker enhances an anchor every two to four source frames and interpolates the frames between. Faster processing can return to enhancing every frame. Shot-change detection avoids blending across cuts, and seeks reset the frame history.

This helps processing keep up, but can introduce motion artifacts. It cannot compensate for an encoder that is itself too slow. Disabling interpolation processes every source frame and may need lower quality or resolution to keep up.

## Startup

Playback starts with a lighter pass while enhancement models load. It normally changes to full enhancement after about eight seconds of video. The same encoder, audio track, resolution, and timestamps continue through the change.

The first segment waits for a small reserve of contiguous segments. This reduces the early pause after playback starts, but startup still needs some buffering. Short clips can end before enhancement takes over.

## Auto HDR

Auto HDR is off by default. Enable **NVIDIA Auto HDR (HDR10 players)** in Advanced settings on a compatible Windows server, then start a new playback session. It uses NVIDIA TrueHDR for eligible SDR input and produces HEVC Main 10, 10-bit BT.2020/PQ HDR10 video with AAC audio. Existing HDR sources use normal Jellyfin playback.

The player must explicitly advertise HDR10 support. For supported client profiles without an HEVC constraint profile, the plugin can also accept the display's HDR10 declaration under AV1 or VP9. Explicit HEVC restrictions still take priority. Unknown capabilities retain SDR.

Playback testing confirmed HDR activation with independently checked HDR10 video and AAC audio. A fresh run passed more than five minutes without rebuffering or stream errors. Check the display's HDR picture mode when validating a player; HEVC Main 10 alone is not proof of HDR activation.

Turn Auto HDR off to keep enhanced SDR output. This setting does not alter the original file or override the selected input-resolution limit.

## Memory use

Enhancement and interpolation tensors use GPU memory. Encoded HLS segments use the plugin's shared RAM cache, with disk fallback.

Lookahead above 0 uses FFmpeg encoding and copies enhanced frames through RAM: NV12 for SDR or P010 for HDR. Lookahead 0 can use the direct GPU encoder path; client compatibility profiles may select FFmpeg instead.

[Settings](configuration.md) · [Comparisons](comparisons.md) · [Third-party notices](../THIRD_PARTY_NOTICES.md)
