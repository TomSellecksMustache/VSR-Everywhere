# VSR Everywhere

NVIDIA Video Super Resolution for Jellyfin servers.

This plugin processes video on your server and sends the result to your Jellyfin player. Your original files stay as they are. The player needs to support the stream you choose, but it does not need its own AI upscaler.

[**Download the Windows installer — 1.3.1**](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.3.1/VSR-Everywhere-1.3.1-Setup.exe) · [All downloads](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/tag/v1.3.1)

**Linux:** [Download the server installer](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.3.1/Install-VSREverywhere-1.3.1-Linux.sh) · [Linux setup](INSTALL-LINUX.md)

## What it does

- Upscales eligible video with NVIDIA VSR.
- Lets you choose processing and stream resolutions separately, from 1080p to 8K.
- Crops stable black bars from the stream and fits output to the picture's aspect ratio.
- Adds optional Veyra neural rendering on Windows. Automatic mode uses plain VSR for animation and NR+VSR for live action.
- Uses interpolation to help processing keep up. The delivered frame rate still matches the source.
- Keeps working frames in GPU memory and encoded stream segments in RAM, with disk fallback.
- Lets you enable it for all devices or select individual Jellyfin devices.

## What it doesn't do

- It does not replace Jellyfin, install a graphics driver, or change your media files.
- The input path accepts 8-bit or 10-bit SDR video up to 1080p. Other sources use normal Jellyfin playback.
- It does not turn a low-quality source into a perfect one. Strong settings can change texture or introduce artifacts.
- It does not guarantee real-time playback at every resolution or on every GPU.
- It does not provide NVIDIA's official DLSS 5 game integration. The neural-rendering option uses Veyra's experimental community runtime.
- Auto HDR is experimental, off by default, and does not reliably activate HDR on players yet.
- Linux has plain VSR. Neural rendering, Auto HDR, and interpolation are Windows features.

## Install

You need an initialized **Jellyfin server**, compatible **NVIDIA hardware and drivers**, internet access, and room for the downloads and private runtime. Windows uses the install wizard. Linux uses a terminal installer for native systemd services.

On Windows, open the EXE, confirm your Jellyfin server, choose a folder, and install. Setup downloads the dependencies, tests video processing, and briefly restarts Jellyfin to install the plugin. You do not need to install Python or build anything.

[Windows setup](INSTALL-WINDOWS.md) · [Linux setup](INSTALL-LINUX.md) · [Settings](docs/configuration.md) · [Troubleshooting](docs/troubleshooting.md)

There is no fixed free-space minimum. Setup reports an error if storage runs out. The plugin targets the Jellyfin 10.10 API and .NET 8; setup verifies that it loads rather than enforcing one server version.

Start with the default settings. Higher processing resolutions and stronger quality settings need more GPU time. A 4K player can receive 4K while VSR processes at 5K.

## See the difference

**Original (Lanczos) / VSR / NR+VSR**, using the same source frame and crop in each column.

### Animation

![The Venture Bros. — Original (Lanczos), VSR, and NR+VSR](docs/assets/comparisons/dlss/venture-bros-scene-1-detail.webp)

### Live action

![Doctor Who — Original (Lanczos), VSR, and NR+VSR](docs/assets/comparisons/dlss/doctor-who-scene-2-detail.webp)

These use ULTRA, strength 1, 5K processing, and 4K output. HDR and interpolation are off. NR is forced on in the third column for comparison; automatic animation playback uses plain VSR. Open the images to see the full-size crops.

[All eight comparisons and how they were made](docs/comparisons.md)

## Intel/AMD-based upscaling

The separate [testing build](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/tag/v1.0.0-testing.1) adds GPU detection, a backend override, and Intel OpenVINO/Quick Sync upscaling with a public model. Intel GPU playback still needs hardware testing. AMD enhancement is not included yet.

[Testing branch and backend details](https://github.com/TomSellecksMustache/VSR-Everywhere/blob/testing/docs/gpu-backends.md)

## More information

[Documentation](docs/README.md) · [Enhancements](docs/enhancements.md) · [Build from source](docs/development.md) · [Changelog](CHANGELOG.md)

Project code is [MIT licensed](LICENSE). Veyra is a separate GPL component. Other dependencies keep their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). Sample footage belongs to its respective rights holders.
