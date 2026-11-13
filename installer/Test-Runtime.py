"""Prove CUDA, VSR, HEVC Main 10, AAC, and HLS work using generated test media."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--worker", required=True)
    p.add_argument("--ffmpeg", required=True)
    p.add_argument("--ffprobe", required=True)
    p.add_argument("--processing-width", default=3840, type=int)
    p.add_argument("--processing-height", default=2160, type=int)
    p.add_argument("--stream-width", default=3840, type=int)
    p.add_argument("--stream-height", default=2160, type=int)
    p.add_argument("--quality", default="STREAMING_MEDIUM")
    p.add_argument("--report", required=True)
    args = p.parse_args()
    import torch
    import av
    import numpy
    from nvvfx import VideoSuperRes
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable. Install a compatible NVIDIA driver and run setup again.")
    gpu = torch.cuda.get_device_name(0)
    print(f"Checking VSR on {gpu}...", flush=True)
    # Synthetic media only: no library files, user credentials, or server changes.
    with tempfile.TemporaryDirectory(prefix="VsrEverywhere-Check-") as temp:
        root = Path(temp)
        source = root / "source.mp4"
        output = root / "hls" / "index.m3u8"
        subprocess.run([args.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                        "-t", "0.125", "-c:v", "mpeg4", "-q:v", "5", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(source)], check=True, timeout=60)
        cmd = [sys.executable, args.worker, str(source), "--output", str(output),
               "--hls", "--seconds", "0.125", "--ffmpeg", args.ffmpeg, "--ffprobe", args.ffprobe,
               "--segment-type", "mpegts", "--quality", args.quality,
               "--processing-width", str(args.processing_width),
               "--processing-height", str(args.processing_height),
               "--stream-width", str(args.stream_width), "--stream-height", str(args.stream_height)]
        result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        print(result.stdout, flush=True)
        if result.returncode:
            log = output.parent / "ffmpeg-vsr.log"
            if log.exists():
                print(log.read_text(encoding="utf-8", errors="replace")[-8000:], flush=True)
            raise RuntimeError("VSR/HEVC playback test failed. Jellyfin has not been changed.")
        result = subprocess.run([args.ffprobe, "-v", "error", "-show_streams", "-of", "json", str(output)],
                                text=True, capture_output=True, check=True, timeout=60)
        streams = json.loads(result.stdout)["streams"]
        video = next(s for s in streams if s["codec_type"] == "video")
        audio = next(s for s in streams if s["codec_type"] == "audio")
        if (video["codec_name"] != "hevc" or video["width"] != args.stream_width
                or video["height"] != args.stream_height or audio["codec_name"] != "aac"):
            raise RuntimeError("The encoded test stream did not match the requested format.")
        report = {"success": True, "gpu": gpu, "torch": torch.__version__, "python": sys.version,
                  "processing": [args.processing_width, args.processing_height],
                  "stream": [video["width"], video["height"]], "videoCodec": "hevc", "audioCodec": "aac"}
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("VSR, HEVC, AAC, and HLS checks passed.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Setup check failed: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
