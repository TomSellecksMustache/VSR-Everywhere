# VSR Everywhere 1.0.0 - third-party components

The VSR Everywhere integration and setup code is licensed under the [MIT License](LICENSE). GPU libraries, AI models, Python, and the video encoder are downloaded separately from their official publishers during setup. Their licenses remain with the respective publishers. The project license does not apply to sample footage or comparison images derived from it.

- Python: https://www.python.org/psf/license/
- PyTorch: https://github.com/pytorch/pytorch/blob/main/LICENSE
- NVIDIA VFX: https://docs.nvidia.com/maxine/vfx-python/latest/ and https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/
- NVIDIA PyNvVideoCodec: https://docs.nvidia.com/video-technologies/pynvvideocodec/ (downloaded separately; its wheel retains NVIDIA's license notices).
- PyAV: https://github.com/PyAV-Org/PyAV
- NumPy: https://numpy.org/about/
- Jellyfin FFmpeg: https://github.com/jellyfin/jellyfin-ffmpeg (GPL build; corresponding source and build instructions are provided by this publisher).
- Microsoft Visual C++ runtime: https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist

Pinned publisher URLs and checksums are recorded in `installer/downloads.lock.json`. Python wheel distributions retain their bundled license notices in the private runtime. NVIDIA examples were consulted for the VideoSuperRes API: https://github.com/NVIDIA-Maxine/nvidia-vfx-python-samples
