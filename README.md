# VSR Everywhere — NVIDIA RTX Video Super Resolution for Jellyfin

**Server-side AI video upscaling and optional Auto HDR for Jellyfin.**

VSR Everywhere uses NVIDIA RTX Video Super Resolution (VSR) on your media server and streams enhanced video to Roku and other compatible Jellyfin players. Your original media files stay unchanged, and the player does not need its own AI upscaler.

[**Download the Windows installer — 1.5.0**](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.5.0/VSR-Everywhere-1.5.0-Setup.exe) · [All downloads](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/tag/v1.5.0)

**Linux:** [Download the server installer](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.5.0/Install-VSREverywhere-1.5.0-Linux.sh) · [Linux setup](INSTALL-LINUX.md)

[Installation](#install-on-windows-or-linux) · [Features](#video-upscaling-and-playback-features) · [Requirements](#server-and-player-requirements) · [Comparisons](#original-vs-nvidia-vsr) · [FAQ](#frequently-asked-questions) · [Documentation](docs/README.md)

## Video upscaling and playback features

- Upscales eligible SDR video with NVIDIA RTX VSR on the server's GPU.
- Converts eligible SDR input to 10-bit HDR10 with NVIDIA TrueHDR on Windows. HDR playback is confirmed in the regular Jellyfin Roku app.
- Lets you choose processing and stream resolutions separately, from 1080p to 8K.
- Crops stable black bars from the stream and fits output to the picture's aspect ratio.
- Uses interpolation to help processing keep up. The delivered frame rate still matches the source.
- Delivers HEVC video and AAC audio over HLS, with GPU processing and a shared RAM stream cache with disk fallback.
- Lets you enable it for all devices or select individual Jellyfin devices.
- Checks stable GitHub releases and installs compatible updates from plugin settings.

## Server and player requirements

| Component | Requirements |
| --- | --- |
| Jellyfin server | Initialized server. The plugin targets the Jellyfin 10.10 API and .NET 8; compatibility checks cover 10.10, 10.11, and 12.1 APIs. |
| Server GPU | Compatible NVIDIA RTX hardware and drivers for the requested processing and encoding features. Setup tests the runtime. |
| Player | HEVC, AAC, the chosen HLS format, and the delivery resolution. HDR10 support is required for Auto HDR output. |
| Input video | 8-bit or 10-bit SDR, up to 800p by default; optional sources up to 1080p with maximum source width 1920 pixels. |
| Storage and network | Space for dependencies and runtime downloads; sufficient bandwidth for the enhanced stream. Roku uses a 40 Mbps CBR compatibility profile. |

### Playback support and limits

- It does not replace Jellyfin, install a graphics driver, or change your media files.
- The input path accepts 8-bit or 10-bit SDR video up to 800p by default. Enable **Process inputs above 800p** to include sources up to 1080p. Other sources use normal Jellyfin playback.
- It does not turn a low-quality source into a perfect one. Strong settings can change texture or introduce artifacts.
- It does not guarantee real-time playback at every resolution or on every GPU.
- Auto HDR is off by default and requires an explicit HDR10 capability declaration. Unknown capabilities retain SDR. [Auto HDR settings and behavior](docs/enhancements.md#auto-hdr).
- Linux has plain VSR. Auto HDR and interpolation are Windows features.

## Install on Windows or Linux

You need an initialized **Jellyfin server**, compatible **NVIDIA hardware and drivers**, internet access, and room for the downloads and private runtime. Windows uses the install wizard. Linux uses a terminal installer for native systemd services.

On Windows, open the EXE, confirm your Jellyfin server, choose a folder, and install. Setup downloads the dependencies, tests video processing, and briefly restarts Jellyfin to install the plugin. You do not need to install Python or build anything.

[Windows setup](INSTALL-WINDOWS.md) · [Linux setup](INSTALL-LINUX.md) · [Settings](docs/configuration.md) · [Troubleshooting](docs/troubleshooting.md)

There is no fixed free-space minimum. Setup reports an error if storage runs out. The plugin targets the Jellyfin 10.10 API and .NET 8; setup verifies that it loads rather than enforcing one server version.

Start with the default settings. Higher processing resolutions and stronger quality settings need more GPU time. A 4K player can receive 4K while VSR processes at 5K.

Concurrent GPU workloads can reduce processing speed. Seeking to uncached video may require substantial buffering. The supplied installers support native Windows and Linux systemd servers; Docker is outside the installer workflow.

## Original vs NVIDIA VSR

**Original (Lanczos) / VSR**, using the same source frame and crop on each side.

### Animation

![The Venture Bros. animation: original Lanczos frame beside NVIDIA VSR at 5K processing and 4K output](docs/assets/comparisons/venture-bros-scene-1-detail.webp)

### Live action

![Doctor Who live action: original Lanczos frame beside NVIDIA VSR at 5K processing and 4K output](docs/assets/comparisons/doctor-who-scene-2-detail.webp)

These use ULTRA, strength 1, 5K processing, and 4K output. HDR and interpolation are off. Open the images to see the full-size crops.

[All eight comparisons and how they were made](docs/comparisons.md)

## Frequently asked questions

### Does VSR Everywhere work with Jellyfin on Roku?

Yes. The server performs enhancement and sends an HEVC/AAC HLS stream to the normal Jellyfin Roku app. Version 1.5.0 fixes HDR10 detection when Roku advertises display capability under AV1 or VP9 instead of HEVC. The tested Roku profile uses 40 Mbps CBR and five-second segments; the network must sustain that stream.

### Does the playback device need an NVIDIA GPU?

No. NVIDIA processing runs on the Jellyfin server. The player needs to decode the delivered video, audio, resolution, and dynamic range.

### How do I enable Auto HDR?

On a compatible Windows server, enable **NVIDIA Auto HDR (HDR10 players)** in the plugin's Advanced settings, then start a new playback session. Auto HDR stays off by default. [Auto HDR setup and behavior](docs/enhancements.md#auto-hdr).

### Does it re-encode or modify my media library?

No. Enhanced video is generated during playback and kept in the stream cache. Original files are read without modification.

### Can I upscale a 1080p source or deliver 8K video?

Enable **Process inputs above 800p** to accept eligible SDR sources up to 1080p. Processing and output presets extend to 8K, but the server's GPU, encoder, and player must support the chosen sizes. A larger preset is not a promise of real-time processing.

### Are Intel and AMD GPUs supported?

The separate [testing build](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/tag/v1.0.0-testing.1) adds GPU detection, a backend override, and Intel OpenVINO/Quick Sync upscaling with a public model. Intel GPU playback still needs hardware testing. AMD enhancement is not included yet.

[Testing branch and backend details](https://github.com/TomSellecksMustache/VSR-Everywhere/blob/testing/docs/gpu-backends.md)

## Documentation and support

[Documentation](docs/README.md) · [Settings](docs/configuration.md) · [Interpolation and Auto HDR](docs/enhancements.md) · [Troubleshooting](docs/troubleshooting.md) · [Build from source](docs/development.md) · [Changelog](CHANGELOG.md) · [Report an issue](https://github.com/TomSellecksMustache/VSR-Everywhere/issues)

Project code is [MIT licensed](LICENSE). Other dependencies keep their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). Sample footage belongs to its respective rights holders.
