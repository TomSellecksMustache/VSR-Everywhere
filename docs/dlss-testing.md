# DLSS / Veyra testing branch

`testing-dlss` starts from `main`. It is independent of the Intel/AMD `testing` branch. Stable **v1.0.0** remains the recommended download.

This Windows preview integrates [Veyra-NRVideo](https://github.com/Likely7/Veyra-NRVideo)'s experimental community neural-rendering path, including its ShortFuse RTX 30/40 compatibility runtime. It is **not NVIDIA's official DLSS 5 game integration**. NVIDIA's official integration needs game-engine inputs that a Jellyfin video stream does not provide.

## Install

Run the preview `VSR-Everywhere-1.0.0-dlss.3-Setup.exe`, or download the matching Windows ZIP and `Install-VSREverywhere.ps1` together. Choose a drive with **25 GB free**. Setup downloads and verifies Python, GPU libraries, the video encoder, and Veyra's runtime. No compiler, system Python, Veyra GUI, or model download steps are needed on the server.

Use Windows 11 x64 with an NVIDIA RTX GPU, a compatible NVIDIA driver, and a native Jellyfin 12.1 service or desktop installation. The RTX 4070 Ti SUPER is validated; other GPU models still need testing. These new enhancements use DirectX 12 on Windows. The existing Linux VSR path remains available; Veyra neural rendering is not a Linux implementation.

Setup tests the selected processing and stream resolutions before restarting Jellyfin. It preserves existing device approvals and quality settings, backs up the installation, and restores the previous files if Jellyfin fails to load the plugin. Switching back to the stable installer restores the stable worker while retaining compatible settings.

This installer also upgrades manual installations containing only `Jellyfin.Plugin.VsrEverywhere.dll` in the `VSR-Everywhere` plugin folder. It checks the DLL's assembly identity when `meta.json` is absent. Unknown folders are rejected before downloads or a server restart; existing files and configuration are backed up for rollback.

## Settings

| Setting | Behavior |
| --- | --- |
| Veyra experimental neural rendering | Enabled by default in this preview. Runs before the existing NVIDIA VSR stage. |
| Content profile: Automatic | Resolves the currently playing Jellyfin title and its movie/series genres and tags. Animation/anime metadata selects Animation. Other known metadata selects Live action. Missing metadata uses a conservative profile. |
| Content profile override | Animation or Live action forces that profile for every enhanced title. |
| NVIDIA Auto HDR | Off by default. Converts eligible SDR input using NVIDIA TrueHDR when the player explicitly advertises HEVC HDR10 support, in its playback request or registered Jellyfin capabilities. Unknown/SDR players receive SDR. Existing HDR sources keep normal Jellyfin playback. |
| Use interpolation to maintain the input frame rate | Enabled by default. Adapts how often expensive enhancement runs, then synthesizes the frames between enhanced anchors. Output always retains the input frame rate. Disabling it enhances every source frame. |

Animation uses lower neural-rendering intensity, tone, and structure changes to preserve line art. Live action uses stronger reconstruction. Unknown titles use the conservative animation settings. Automatic classification uses library metadata associated with the title; it makes no external title lookup or AI-service request. Add the Animation genre/tag in Jellyfin or use the override when library metadata is incomplete.

Output always matches the input rate, including fractional rates such as 23.976 and 29.97. There are no fixed-rate output modes. When enhancement cannot keep up, the worker processes one source anchor every two to four frames and uses NVIDIA fractional-timestep interpolation to fill the original presentation slots. Faster GPUs can return to enhancing every frame. The resolution, VSR quality, NR profile, HDR setting, and encoder quality remain selected by the user. NVIDIA shot-change detection guards interpolation across cuts. Seek resets clear neural-rendering history and the two-frame GPU cache while retaining loaded models and timing estimates.

## Processing and limits

Memory-HLS playback starts with a lightweight preview while enhancement models warm in the background. Preview frames retain the input cadence and use the final HEVC Main 10 resolution, audio track, and color format. HDR preview uses a real BT.709/sRGB-to-BT.2020/PQ conversion with 203-nit SDR white. The worker changes to NR/VSR/TrueHDR/interpolation at a keyframe, normally after eight seconds of preview. One encoder and muxer remain active throughout, so codec, audio, timestamps, and HDR mode do not change at the handoff. Short videos can finish during the preview. Seeking retains loaded models.

The server holds the first requested media segment until three contiguous segments are cached (or the source ends). This reserve covers model warmup and the encoder's initial delay. On-demand HLS limits FFmpeg's shortest-stream buffer to one second, avoiding a delivery stall when the worker pauses its lookahead. CPU imports, encoding, and downloading the first segments still take time; playback is not literally instantaneous. Cold model loading that exceeds the reserve can still require buffering.

Roku delivery is capped at **40 Mbps** without changing the saved bitrate for other players. Both encoding paths now use the selected bitrate as their VBV maximum instead of allowing a 30% increase. Short bursts can exceed that average within the VBV window. This follows [Roku's HEVC streaming limits](https://developer.roku.com/dev/docs/media), which also prohibit mixing SDR/HDR variants and seamless changes between different codecs.

The pipeline is decode → Veyra neural rendering → NVIDIA VSR → delivery resize → optional TrueHDR → optional frame interpolation → HEVC Main 10 + AAC → HLS. HDR output carries BT.2020/PQ signaling. The existing memory cache, disk fallback, device authorization, audio selection restrictions, and seek-on-demand behavior are retained.

Veyra runs in a separate resident process. Its input/output frames cross host RAM through pipes; there is no uncompressed disk cache. Enhancement and interpolation tensors remain on the GPU, with at most two enhanced source frames retained. With NVENC lookahead enabled, enhanced SDR and HDR frames are converted to P010 on the GPU before transfer to FFmpeg. With lookahead disabled and a compatible runtime, CUDA P010 conversion feeds NVENC directly. Compressed HLS remains in the shared RAM cache unless disk mode is selected.

Adaptive sampling targets the input frame budget with headroom, up to four source frames per enhanced anchor. This trades reconstruction on individual source frames for real-time delivery at the original cadence. It can introduce interpolation artifacts during difficult motion. If encoding alone exceeds the frame budget, interpolation cannot make the whole pipeline real time. Check worker `effective_fps` against the source rate and `enhancement_stride` for the current sampling interval.

On the RTX 4070 Ti SUPER, a six-second synthetic 1080p benchmark at 5K → 4K, ULTRA, strength 1, p7/hq, lookahead 12, CQ 1, and 60 Mbps rendered **24.06 output fps** with NR, HDR, and adaptive interpolation active. The output retained the **24000/1001 input rate**. The worker settled at one enhanced anchor every three source frames. This is a short synthetic measurement, not a guarantee for every movie or GPU.

Six-second clips from the supplied 720p animation and live-action samples rendered **31.14 fps** and **26.91 fps**, respectively, with the same quality settings and all enhancements active. Both encoded streams retained **24000/1001** and BT.2020/PQ signaling. These short checks do not replace watching a full title for interpolation artifacts or sustained thermal limits.

## Build and licenses

Run `powershell -ExecutionPolicy Bypass -File Build-Installer.ps1` with .NET 10 SDK, Visual Studio 2022 C++ Build Tools, CMake, a Windows SDK with DXC, Git, and internet access. Outputs go to `dist/dlss-preview`, leaving existing stable and Intel preview packages separate.

`Build-Veyra.ps1` pins the upstream source and NVIDIA SDK commits and verifies FFmpeg/NVOF download hashes. Its headless build patch skips Veyra's GUI/media applications without replacing the processing graph. The component ships its GPL license and complete corresponding-source ZIP. The Jellyfin plugin and original Python integration remain MIT. NVIDIA libraries/models and the community compatibility runtime retain their own terms; setup fetches the original Veyra runtime from its upstream release rather than rehosting it here. See [third-party notices](../THIRD_PARTY_NOTICES.md) and [native build details](../native/veyra/README.md).

## Validation

Validation uses real Veyra inference, TrueHDR BT.2020/PQ output, GPU and preserved lookahead encoding, source-rate timing checks, memory-HLS seeks, fMP4 and MPEG-TS, and installer rollback/custom-drive fixtures. Linux neural rendering, other NVIDIA GPUs, and end-to-end HDR switching on physical Jellyfin clients are not validated.
