# Veyra neural-rendering filter

This independent Windows command-line filter reuses Veyra's production `EnhanceGraph`. It is a GPL-3.0 component, separate from the MIT Jellyfin plugin and Python integration. Preserve the upstream license, notices, and corresponding source when distributing its executable.

Upstream: https://github.com/Likely7/Veyra-NRVideo

Pinned source: `8332c8a8382242096a2699d3ba4dd52ad47bbd74`.

Runtime: upstream's hash-verified NVIDIA 2.1.5 package and experimental ShortFuse compatibility path. It is community neural rendering, not a certified NVIDIA DLSS 5 video integration.

The filter accepts framed NV12 video over stdin and returns sRGB RGBA8 pixels over stdout. Veyra's NR/optical-flow state stays resident until shutdown. A reset flag clears history on seeks. The caller handles final super resolution, HDR, frame interpolation, encoding, and audio. This process boundary transfers uncompressed frames through host RAM; it does not write them to disk.

The headless CMake patch stops configuration before GUI/media applications; it preserves the upstream processing graph. Runtime, SDK headers/libraries, and model files are fetched separately and stay outside source Git.

Build from the repository root with `powershell -File Build-Veyra.ps1`. Requires Visual Studio 2022 C++ Build Tools, a Windows SDK with DXC, CMake 3.24+, Git, and internet access. The script pins source/SDK commits and verifies download hashes. It writes the executable, GPL license, and complete corresponding-source ZIP into `installer/build/veyra-component`. The source ZIP contains this adapter, the pinned upstream source archive, build script, and upstream notices.
