# VSR Everywhere 1.1.0 - third-party components

The original VSR Everywhere integration and setup code is licensed under the [MIT License](LICENSE). GPU libraries, AI models, Python, and the video encoder are downloaded separately during setup. Their licenses remain with the respective publishers. The project license does not apply to sample footage or comparison images derived from it.

The Windows package includes a separate **GPL-3.0 Veyra command-line filter**, built from Likely7/Veyra-NRVideo commit `8332c8a8382242096a2699d3ba4dd52ad47bbd74` and the adapter in `native/veyra`. Its GPL license and complete corresponding-source ZIP accompany the executable. Veyra's upstream notices are retained in that source ZIP and its downloaded runtime. This component is not covered by the project's MIT license.

Veyra's experimental ShortFuse neural-rendering compatibility runtime contains community-modified NVIDIA components. Setup downloads the pinned upstream 2.1.5 NVIDIA package; these binaries/models are not committed to this repository or republished in its installer. Their availability does not change their publishers' licensing terms. This is experimental community neural rendering, not an official NVIDIA DLSS 5 video product.

- Python: https://www.python.org/psf/license/
- Linux portable Python distribution: https://github.com/astral-sh/python-build-standalone (its bundled license notices are retained in the private runtime).
- PyTorch: https://github.com/pytorch/pytorch/blob/main/LICENSE
- NVIDIA VFX: https://docs.nvidia.com/maxine/vfx-python/latest/ and https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/
- NVIDIA PyNvVideoCodec: https://docs.nvidia.com/video-technologies/pynvvideocodec/ (downloaded separately; its wheel retains NVIDIA's license notices).
- PyAV: https://github.com/PyAV-Org/PyAV
- NumPy: https://numpy.org/about/
- Jellyfin FFmpeg: https://github.com/jellyfin/jellyfin-ffmpeg (GPL build; corresponding source and build instructions are provided by this publisher).
- Microsoft Visual C++ runtime: https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist

Pinned publisher URLs and checksums are recorded in `installer/downloads.lock.json`. Python wheel distributions retain their bundled license notices in the private runtime. NVIDIA examples were consulted for the VideoSuperRes API: https://github.com/NVIDIA-Maxine/nvidia-vfx-python-samples
