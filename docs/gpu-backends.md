# GPU detection and backend override

Open **Dashboard → Plugins → VSR Everywhere → GPU backend override**.

| Selection | Behaviour |
| --- | --- |
| Automatic (recommended) | Prefers a ready NVIDIA backend, then Intel. No CPU fallback. |
| NVIDIA Video Super Resolution + NVENC | Requires NVIDIA; never changes vendor silently. |
| Intel Super Resolution (OpenVINO) + Quick Sync | Requires Intel; never changes vendor silently. |

New installations default to Automatic. Existing saved overrides, quality, resolutions, and device selections are retained. A missing or unsupported backend leaves standard Jellyfin playback available. GPU readiness is cached for one minute; changing the override or executable paths invalidates that cache.

## Installation

The Windows wizard and Linux systemd installer download the private runtimes required by detected GPUs. Intel-only systems do not download CUDA/PyTorch. Systems with both vendors receive both runtimes, allowing an override without manual dependency installation. Rerun setup after adding another GPU.

Setup downloads the model, checks SHA-256 hashes, and tests enhancement, HEVC Main 10, AAC, and HLS before changing Jellyfin. Drivers must already be installed; setup does not replace graphics or kernel drivers.

Intel requires an Intel GPU accessible to OpenVINO and a working HEVC Main 10 Quick Sync encoder. On Linux, install the Intel compute and media drivers for the distribution and give the Jellyfin service read/write access to the Intel `/dev/dri/renderD*` device. Setup runs its video check as the service account. See [OpenVINO GPU setup](https://docs.openvino.ai/2026/get-started/install-openvino/configurations/configurations-intel-gpu.html) and [Jellyfin Intel acceleration](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/intel/).

## Intel processing

Intel uses OpenVINO 2026.1.0 and the public Apache-2.0 [single-image-super-resolution-1033 model](https://github.com/openvinotoolkit/open_model_zoo/tree/master/models/intel/single-image-super-resolution-1033). This is a 3× image enhancement model, followed by resizing to the selected processing resolution. Stream delivery resolution remains independent.

This backend uses the public OpenVINO model rather than Intel's gated iVSR model packages. Setup downloads its FP16 XML/BIN files and Apache license from official sources.

Inference, bicubic model input preparation, strength blending, and processing resize run on the Intel GPU. The model remains loaded across seeks. Decoded and enhanced RGB frames pass through system RAM before Quick Sync HEVC encoding. This is not a zero-copy pipeline.

Both vendors use the existing frame-pacing, seek-regeneration, AAC audio, and bounded RAM HLS cache. Strength and video bitrate affect both backends. NVIDIA quality modes and NVENC controls apply only to NVIDIA.

## Validation limits

Intel model shape, colour, detail, and worker/audio muxing have CPU/software reference tests. This development machine has no Intel GPU. Intel GPU inference, Quick Sync playback, and real-time performance still require validation on an Intel server. Large resolution presets depend on GPU memory and encoder limits.
