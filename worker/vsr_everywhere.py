"""VSR Everywhere: NVIDIA Video Effects SDK (Maxine) -> NVENC -> HLS.

Windows/Linux worker. Supports 8-bit SDR source video and the first
(default) audio stream. A single process handles one session. No network API.
Copyright 2026 VSR Everywhere contributors. This file is original integration code.
NVIDIA's SDK/pip packages and models are installed independently under their terms.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from fractions import Fraction
import json
import ipaddress
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit
# Embeddable Python deliberately omits the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vsr_metrics import StageMetrics, process_resources
from vsr_capabilities import probe_codec
from vsr_threaded import write_all
from vsr_server import RunCancelled, cfr_frames, cfr_origin
from vsr_crop import FrameCrop

TARGET_W, TARGET_H = 3840, 2160  # default HEVC delivery canvas
QUALITY = ("STREAMING_MEDIUM", "STREAMING_ULTRA", "HIGH", "MEDIUM", "LOW", "ULTRA",
           "HIGHBITRATE_MEDIUM", "HIGHBITRATE_HIGH", "HIGHBITRATE_ULTRA")


@dataclass(frozen=True)
class Geometry:
    scale: int
    vsr_width: int
    vsr_height: int
    displayed_width: int
    displayed_height: int


def geometry(width: int, height: int, sar_num: int = 1, sar_den: int = 1,
             processing_width: int = 3840, processing_height: int = 2160) -> Geometry:
    """Keep display aspect ratio. GPU VSR stage is 2-4x, letterbox via encoder."""
    if not (1 <= width <= 3840 and 1 <= height <= 2160 and sar_num > 0 and sar_den > 0):
        raise ValueError("Only SDR video from 1x1 up to 3840x2160 is supported")
    # A/V decode displays sample-aspect-ratio pixels; de-anamorphose in FFmpeg
    # after VSR rather than misleading clients with square-pixel source values.
    ratio = min(processing_width / (width * sar_num / sar_den), processing_height / height)
    factor = min(4, max(2, math.ceil(ratio)))
    virtual_w = width * sar_num / sar_den
    fit = min(processing_width / virtual_w, processing_height / height)
    disp_w = max(2, int(virtual_w * fit) // 2 * 2)
    disp_h = max(2, int(height * fit) // 2 * 2)
    # Feed the selected active-picture dimensions to the AI, with aspect padding later.
    vsr_w = max(2, (int(disp_w * sar_den / sar_num) // 2) * 2)
    vsr_h = disp_h
    if vsr_w < width or vsr_h < height:
        raise ValueError("processing resolution must not be smaller than the original video")
    return Geometry(factor, vsr_w, vsr_h, disp_w, disp_h)


def validate_memory_output(dest: str, key: str) -> None:
    if any(ord(char) < 32 or ord(char) == 127 for char in dest):
        raise ValueError("Invalid memory ingest URL")
    url = urlsplit(dest)
    if (url.scheme not in {"http", "https"} or url.username is not None or url.password is not None
            or url.query or url.fragment or not url.port
            or not ipaddress.ip_address(url.hostname or "").is_loopback):
        raise ValueError("Memory ingest must use a loopback HTTP(S) URL with an explicit port")
    _, separator, tail = url.path.rpartition("/VsrEverywhere/ingest/")
    if (not separator or not re.fullmatch(r"[0-9a-fA-F]{48}(?:/[0-9]{1,10})?/index\.m3u8", tail)
            or any(unquote(part) in {".", ".."} or "/" in unquote(part) or "\\" in unquote(part)
                   for part in url.path.split("/"))):
        raise ValueError("Invalid memory ingest path")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
        raise ValueError("Memory ingest requires VSR_INGEST_KEY")


def build_ffmpeg_command(ffmpeg: str, source: str, dest: str, width: int, height: int,
                         fps: Fraction, mbps: int, segment_type: str, start: float = 0,
                         hls: bool = True, sar: Fraction = Fraction(1, 1),
                         nvenc_preset: str = "p4", nvenc_tune: str = "default",
                         nvenc_lookahead: int = 0, nvenc_spatial_aq: bool = False,
                         nvenc_temporal_aq: bool = False, nvenc_cq: int = 0,
                         stream_width: int = 3840, stream_height: int = 2160,
                         rolling_rewind_minutes: int = 0, encoded_video: bool = False,
                         raw_pixel_format: str = "rgb24", memory_output: bool = False,
                         ingest_key: str = "", start_segment: int | None = None,
                         source_start_time: float = 0, hdr_output: bool = False) -> list[str]:
    if memory_output:
        if not hls:
            raise ValueError("Memory output requires HLS")
        validate_memory_output(dest, ingest_key)
    if segment_type not in {"fmp4", "mpegts"}:
        raise ValueError("invalid segment type")
    if not (4 <= mbps <= 60 and 0 <= start < 1_000_000 and 1 <= fps <= 120):
        raise ValueError("invalid encode settings")
    if nvenc_preset not in {"p4", "p5", "p6", "p7"} or nvenc_tune not in {"default", "hq"}:
        raise ValueError("invalid NVENC preset/tune")
    if not (0 <= nvenc_lookahead <= 32 and 0 <= nvenc_cq <= 35):
        raise ValueError("invalid NVENC lookahead/CQ")
    # VSR output frames are RGB. Their CPU transfer is a CPU transfer step between the enhancement and encoding stages.
    fps_str = f"{fps.numerator}/{fps.denominator}"
    vf = f"setsar={sar.numerator}/{sar.denominator},scale=w='max(2,round(min({stream_width},{stream_height}*dar)/2)*2)':" \
         f"h='max(2,round(min({stream_height},{stream_width}/dar)/2)*2)':flags=lanczos," \
         f"pad={stream_width}:{stream_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=p010le"
    if raw_pixel_format not in {"rgb24", "nv12", "p010le"}:
        raise ValueError("invalid raw pixel format")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-y"]
    if start_segment is not None:
        cmd += ["-copyts", "-itsoffset", f"{start:.9f}"]
    if encoded_video:
        cmd += ["-probesize", "32768", "-analyzeduration", "0", "-f", "mpegts", "-i", "pipe:0"]
    else:
        cmd += ["-f", "rawvideo", "-pixel_format", raw_pixel_format, "-video_size", f"{width}x{height}",
                "-framerate", fps_str]
        if raw_pixel_format in {"nv12", "p010le"} and not hdr_output:
            cmd += ["-colorspace", "bt709", "-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709"]
        if hdr_output:
            cmd += ["-colorspace", "bt2020nc", "-color_range", "tv", "-color_primaries", "bt2020", "-color_trc", "smpte2084"]
        cmd += ["-i", "pipe:0"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if start_segment is not None and source_start_time:
        cmd += ["-itsoffset", f"{-source_start_time:.9f}"]
    cmd += ["-i", source, "-map", "0:v:0", "-map", "1:a:0?"]
    if encoded_video:
        colour = "colour_primaries=9:transfer_characteristics=16:matrix_coefficients=9" if hdr_output else "colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1"
        cmd += ["-c:v", "copy", "-tag:v", "hvc1", "-bsf:v",
                "hevc_metadata=video_full_range_flag=0:" + colour]
    else:
        cmd += ["-vf", vf, "-c:v", "hevc_nvenc", "-pix_fmt", "p010le",
            "-profile:v", "main10", "-tag:v", "hvc1",
            "-b:v", f"{mbps}M", "-maxrate", f"{mbps}M",
            "-bufsize", f"{int(mbps * 2)}M", "-g", str(round(float(fps) * 2))]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest"]
    if not encoded_video:
        cmd += (["-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", "-color_range", "tv"] if hdr_output
                else ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"])
    cmd += ["-metadata:s:v:0", "title=VSR Everywhere", "-max_muxing_queue_size", "1024"]
    if not encoded_video:
        cmd += ["-preset", nvenc_preset]
    if not encoded_video and nvenc_tune == "hq":
        cmd += ["-tune", "hq"]
    if not encoded_video and nvenc_lookahead:
        cmd += ["-rc-lookahead", str(nvenc_lookahead)]
    if not encoded_video and nvenc_spatial_aq:
        cmd += ["-spatial-aq", "1"]
    if not encoded_video and nvenc_temporal_aq:
        cmd += ["-temporal-aq", "1"]
    if not encoded_video and nvenc_cq:
        cmd += ["-rc", "vbr", "-cq", str(nvenc_cq)]
    if start_segment is not None:
        if not 0 <= start_segment <= 999999:
            raise ValueError("Invalid starting segment")
        cmd += ["-avoid_negative_ts", "make_non_negative", "-muxdelay", "0", "-muxpreload", "0",
                "-shortest_buf_duration", "1"]
        if not encoded_video:
            cmd += ["-forced-idr", "1", "-no-scenecut", "1", "-bf", "0"]
    if hls:
        segment_path = (dest.rsplit("/", 1)[0] + "/seg_%06d" if memory_output
                        else str(Path(dest).parent / "seg_%06d"))
        # Rolling mode uses FFmpeg's HLS sliding playlist. Do not label it EVENT/VOD:
        # those playlist types cannot remove historic entries safely.
        seg_count = max(60, rolling_rewind_minutes * 30) if rolling_rewind_minutes else 0
        hls_flags = "independent_segments" + ("" if memory_output else "+temp_file")
        if rolling_rewind_minutes:
            hls_flags += "+delete_segments"
        cmd += ["-f", "hls", "-hls_time", "1.9" if start_segment is not None else "2",
                "-hls_list_size", str(seg_count), "-hls_flags", hls_flags]
        if start_segment is not None:
            cmd += ["-start_number", str(start_segment)]
        if not rolling_rewind_minutes:
            cmd += ["-hls_playlist_type", "event"]
        if segment_type == "fmp4":
            cmd += ["-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
                    "-hls_segment_filename", segment_path + ".m4s"]
            if start_segment is not None:
                cmd += ["-hls_segment_options", "movflags=+frag_discont+skip_sidx"]
        else:
            cmd += ["-hls_segment_type", "mpegts",
                    "-hls_segment_filename", segment_path + ".ts"]
        if memory_output:
            cmd += ["-method", "PUT", "-http_persistent", "1", "-headers", f"X-Vsr-Key: {ingest_key}\r\n"]
        cmd += [dest]
    else:
        cmd += ["-movflags", "+faststart", dest]
    return cmd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VSR Everywhere NVIDIA VSR on-demand HLS worker")
    p.add_argument("source", help="Read-only source media path")
    p.add_argument("--output", required=True)
    p.add_argument("--hls", action="store_true")
    p.add_argument("--memory-output", action="store_true", help="Upload HLS to the plugin's loopback memory store")
    p.add_argument("--serve", action="store_true", help="Keep VSR loaded and accept seek commands on stdin")
    p.add_argument("--backend", default="nvidia", choices=["nvidia", "vsr"])
    p.add_argument("--neural-rendering", action="store_true")
    p.add_argument("--content-profile", choices=['animation', 'live_action', 'unknown'], default='unknown')
    p.add_argument("--auto-hdr", action="store_true")
    p.add_argument("--frame-interpolation", action="store_true")
    runtime_filter = Path(sys.executable).resolve().parent / 'veyra' / 'VsrVeyraFilter.exe'
    p.add_argument("--veyra-filter", default=str(runtime_filter if runtime_filter.is_file() else Path(__file__).resolve().parent / 'veyra' / 'VsrVeyraFilter.exe'))
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--ffprobe", default="ffprobe")
    p.add_argument("--video-mbps", type=int, default=18)
    p.add_argument("--segment-type", default="fmp4", choices=["fmp4", "mpegts"])
    p.add_argument("--quality", default="STREAMING_MEDIUM", choices=QUALITY)
    p.add_argument("--strength", type=float, default=0.75)
    p.add_argument("--no-vsr", action="store_true", help="Skip NVIDIA VSR while retaining other selected features")
    p.add_argument("--nvenc-preset", default="p4", choices=["p4", "p5", "p6", "p7"])
    p.add_argument("--nvenc-tune", default="default", choices=["default", "hq"])
    p.add_argument("--nvenc-lookahead", type=int, default=0)
    p.add_argument("--nvenc-cq", type=int, default=0)
    p.add_argument("--nvenc-spatial-aq", action="store_true")
    p.add_argument("--nvenc-temporal-aq", action="store_true")
    p.add_argument("--seconds", type=float, default=0)
    p.add_argument("--start-time", type=float, default=0)
    p.add_argument("--processing-width", type=int, default=3840)
    p.add_argument("--processing-height", type=int, default=2160)
    p.add_argument("--stream-width", type=int, default=3840)
    p.add_argument("--stream-height", type=int, default=2160)
    p.add_argument("--crop", help="Fixed source rectangle x:y:width:height for this session")
    p.add_argument("--rolling-rewind-minutes", type=int, default=0)
    p.add_argument("--probe", action="store_true", help="Print GPU/SDK capabilities and exit")
    p.add_argument("--pipeline", choices=["gpu", "legacy"], default="gpu")
    p.add_argument("--legacy-schedule", choices=["serial", "threaded"], default="serial",
                   help="Select the preserved serial path or bounded NV12 fallback for comparison")
    return p.parse_args()


def run(args: argparse.Namespace, retained=None, initialize_only=False, control=None, run_id=0, start_segment=None) -> None:
    ingest_key = os.environ.get("VSR_INGEST_KEY", "") if args.memory_output else ""
    if args.memory_output:
        if not args.hls:
            raise ValueError("Memory output requires HLS")
        validate_memory_output(args.output, ingest_key)
    if not 0 <= args.strength <= 1:
        raise ValueError("strength must be 0..1")
    if not (0 <= args.seconds <= 36000 and args.start_time >= 0):
        raise ValueError("invalid timeline")
    if not (1280 <= args.processing_width <= 7680 and 720 <= args.processing_height <= 4320
            and 16 <= args.stream_width <= 7680 and 16 <= args.stream_height <= 4320
            and all(d % 2 == 0 for d in (args.processing_width, args.processing_height,
                                           args.stream_width, args.stream_height))
            and 0 <= args.rolling_rewind_minutes <= 60):
        raise ValueError("invalid processing/delivery resolution or rewind window")
    # Import heavyweight SDK packages only after syntax/argument validation.
    import av
    import numpy as np
    import torch
    from nvvfx import VideoSuperRes
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable in PyTorch; NVIDIA driver/torch install required")
    if args.probe:
        print(json.dumps({"cuda": True, "gpu": torch.cuda.get_device_name(0),
                          "vfx": "importable", "quality": args.quality,
                          "pynvvideocodec": probe_codec()}))
        return
    source = Path(args.source).resolve(strict=True)
    if not source.is_file():
        raise ValueError("Source must be a regular file")
    dest = args.output if args.memory_output else Path(args.output).resolve()
    if not args.memory_output:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source == dest or source.is_relative_to(dest.parent):
            raise ValueError("Output must be in a separate cache directory, not alongside input")
    container = av.open(str(source))
    proc = None
    log_file = None
    effect = None
    enhancements = None
    encoder = None
    stats = StageMetrics()
    pipe_bytes = 0
    processed = 0
    try:
        if not container.streams.video:
            raise RuntimeError("No video stream")
        stream = container.streams.video[0]
        width = stream.codec_context.width
        height = stream.codec_context.height
        if width > 1920 or height > 1080:
            raise RuntimeError("Initial version accepts <=1920x1080 video; leave 4K originals unchanged")
        sar = stream.sample_aspect_ratio or Fraction(1, 1)
        source_format = getattr(stream.codec_context, 'format', None)
        source_depth = max((c.bits for c in getattr(source_format, 'components', ())), default=8)
        if source_depth > 10:
            raise RuntimeError("Only 8-bit and 10-bit SDR sources are supported")
        if stream.codec_context.color_trc not in (None, 0, 1, 2):
            raise RuntimeError("Non-SDR color transfer; use standard Jellyfin playback")
        crop_frame = FrameCrop(getattr(args, 'crop', None), width, height)
        width, height = crop_frame.width, crop_frame.height
        g = geometry(width, height, sar.numerator, sar.denominator,
                     args.processing_width, args.processing_height)
        fps = Fraction(stream.average_rate or getattr(stream, 'guessed_rate', None)
                       or getattr(stream, 'base_rate', None) or 24).limit_denominator(1001)
        if not (1 <= fps <= 120):
            raise RuntimeError("Unsupported input framerate")
        source_fps = fps
        from vsr_temporal import source_pairs
        advanced = args.neural_rendering or args.auto_hdr or args.frame_interpolation or args.serve or getattr(args, 'no_vsr', False)
        duration = 0
        if initialize_only or advanced:
            duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration or 0) / 1_000_000
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("On-demand playback requires a source with a known duration")
        if initialize_only:
            retained['frame_origin'] = cfr_origin(container, stream, source_fps)
            retained['ready'] = {"event": "ready", "fps_num": fps.numerator, "fps_den": fps.denominator,
                                 "gop_frames": round(2 * fps), "duration_s": duration,
                                 "source_start_time": float(container.start_time or 0) / 1_000_000}
        pipeline = "legacy"
        if args.pipeline == "gpu" and not initialize_only:
            try:
                from vsr_gpu import GpuEncoder
                encode_geometry = replace(g, vsr_width=args.stream_width, vsr_height=args.stream_height) if advanced else g
                encoder = GpuEncoder(args, encode_geometry, fps, Fraction(1) if advanced else sar)
                pipeline = "gpu"
            except Exception as exc:
                print(json.dumps({"event": "fallback", "reason": f"{type(exc).__name__}: {exc}",
                                  "pipeline": "legacy"}), flush=True)
        threaded = not advanced and retained is None and encoder is None and args.legacy_schedule == "threaded"
        def create_effect(stream_ptr=0):
            if getattr(args, 'no_vsr', False):
                return None
            created = VideoSuperRes(quality=VideoSuperRes.QualityLevel[args.quality],
                                   strength=args.strength, device=0,
                                   image_encoding=VideoSuperRes.ImageEncoding.RGB8)
            try:
                created.input_width, created.input_height = width, height
                created.output_width, created.output_height = g.vsr_width, g.vsr_height
                if stream_ptr:
                    # shortcut: nvvfx exposes pre-load stream binding only through _effect; use a public setter when available.
                    created._effect.set_cuda_stream(stream_ptr)
                created.load()
                return created
            except BaseException:
                created.close()
                raise
        if initialize_only:
            from vsr_startup import Startup
            def load_models():
                from vsr_enhancements import Enhancements
                warm_effect = warm_enhancements = None
                warm_stats = StageMetrics()
                stage = 'VSR model loading'
                try:
                    torch.cuda.set_device(0)
                    # shortcut: pinned TrueHDR rejects custom streams; revisit after a verified SDK update.
                    warm_stream = torch.cuda.default_stream(device=0)
                    with torch.cuda.stream(warm_stream):
                        warm_effect = create_effect(warm_stream.cuda_stream)
                        stage = 'NR/HDR/interpolation model loading'
                        warm_enhancements = Enhancements(args, width, height, source_fps)
                        frame = av.VideoFrame.from_ndarray(np.zeros((height, width, 3), dtype=np.uint8), format='rgb24')
                        alpha = Fraction(1, 2) if args.frame_interpolation else Fraction(0)
                        stage = 'first enhanced frame'
                        warm_enhancements.frame((frame, frame, alpha, 0, 0, int(bool(alpha))), source_fps,
                            warm_effect, sar, warm_stream.cuda_stream, warm_stats)
                        warm_stats.resolve()
                        warm_enhancements.reset()
                    return warm_effect, warm_enhancements
                except Exception as exc:
                    events = warm_stats.take_events()
                    if events:
                        stage = events[-1][0]
                    if warm_enhancements is not None:
                        warm_enhancements.close()
                    if warm_effect is not None:
                        warm_effect.close()
                    raise RuntimeError(f'Enhancement warmup failed during {stage}: {exc}') from exc
            retained['startup'] = Startup(args, fps, load_models)
            print(json.dumps(retained['ready']), flush=True)
            return
        startup = retained.get('startup') if retained is not None else None
        if not threaded and startup is None:
            effect = retained.get('effect') if retained is not None else None
            if effect is None:
                effect = create_effect()
                if retained is not None:
                    retained['effect'] = effect
        if startup is not None:
            enhancements = startup
            enhancements.reset()
        elif advanced:
            from vsr_enhancements import Enhancements
            enhancements = retained.get('enhancements') if retained is not None else None
            if enhancements is None:
                enhancements = Enhancements(args, width, height, source_fps)
                if retained is not None:
                    retained['enhancements'] = enhancements
            enhancements.reset()
        seek_time = max(0, args.start_time - float(1 / source_fps)) if advanced else args.start_time
        if args.start_time:
            container.seek(int(seek_time / stream.time_base) + (getattr(stream, 'start_time', None) or 0),
                           stream=stream, any_frame=False, backward=True)
        if not args.memory_output:
            log_file = open(dest.parent / "ffmpeg-vsr.log", "wb")
        cmd = build_ffmpeg_command(args.ffmpeg, str(source), str(dest),
                                   args.stream_width if advanced else g.vsr_width, args.stream_height if advanced else g.vsr_height, fps, args.video_mbps,
                                   args.segment_type, args.start_time, args.hls, Fraction(1) if advanced else sar,
                                   args.nvenc_preset, args.nvenc_tune, args.nvenc_lookahead,
                                   args.nvenc_spatial_aq, args.nvenc_temporal_aq, args.nvenc_cq,
                                   args.stream_width, args.stream_height, args.rolling_rewind_minutes,
                                   encoded_video=encoder is not None,
                                   raw_pixel_format="p010le" if advanced else "nv12" if threaded else "rgb24",
                                   memory_output=args.memory_output, ingest_key=ingest_key, start_segment=start_segment,
                                   source_start_time=retained['ready'].get('source_start_time', 0) if retained is not None else 0,
                                   hdr_output=args.auto_hdr)
        print(json.dumps({"event": "start", "source_size": [width, height],
                          "vsr_size": [g.vsr_width, g.vsr_height],
                          "output_size": [args.stream_width, args.stream_height],
                          "quality": args.quality, "strength": args.strength, "vsr_enabled": not getattr(args, 'no_vsr', False),
                          "nvenc": {"preset": args.nvenc_preset, "tune": args.nvenc_tune,
                                    "lookahead": args.nvenc_lookahead, "cq": args.nvenc_cq,
                                    "spatial_aq": args.nvenc_spatial_aq,
                                    "temporal_aq": args.nvenc_temporal_aq},
                          "fps": float(fps),
                          "source_fps": float(source_fps), "neural_rendering": args.neural_rendering,
                          "content_profile": args.content_profile, "hdr": args.auto_hdr,
                          "frame_interpolation": args.frame_interpolation,
                          "pipeline": pipeline,
                          "legacy_schedule": "threaded" if threaded else "serial",
                          "audio": bool(container.streams.audio)}), flush=True)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=log_file, cwd=None if args.memory_output else str(dest.parent),
                                bufsize=0, creationflags=(0x08000000 if os.name == "nt" else 0))
        if control is not None:
            control.attach(proc, run_id)
        start_wall = time.monotonic()
        cpu_start = time.process_time()
        torch.cuda.reset_peak_memory_stats()
        max_frames = int(args.seconds * float(fps)) if args.seconds else 0
        stream_ptr = torch.cuda.current_stream().cuda_stream
        if threaded:
            from vsr_threaded import run_threaded
            def progress(count, size):
                if count % 60 == 0:
                    elapsed = time.monotonic() - start_wall
                    print(json.dumps({"event": "progress", "frames": count,
                                      "effective_fps": round(count / max(elapsed, .001), 2),
                                      "stages_ms": stats.means(), "pipe_bytes": size,
                                      "pipe_mbps": round(size * 8 / max(elapsed, .001) / 1e6, 3),
                                      "cuda_memory_allocated": torch.cuda.memory_allocated(),
                                      "host_memory": process_resources()}), flush=True)
            processed, pipe_bytes = run_threaded(container, stream, create_effect, torch, np, g, args,
                                                 proc, stats, progress)
        else:
            gop = round(2 * fps)
            if advanced:
                first_output = start_segment * gop if control is not None else math.ceil(args.start_time * float(fps))
                first_source = first_output
                source_frames = cfr_frames(container, stream, source_fps, first_source,
                    math.ceil(duration * float(source_fps)), frame_origin=retained.get('frame_origin') if retained else None)
                source_frames = ((crop_frame(frame), slot) for frame, slot in source_frames)
                frames = iter(source_pairs(source_frames, first_output, math.ceil(duration * float(fps)), lambda: enhancements.stride))
            else:
                frames = iter(cfr_frames(container, stream, fps, start_segment * gop,
                                     math.ceil(retained['ready']['duration_s'] * float(fps)),
                                     frame_origin=retained.get('frame_origin'))
                          if control is not None else container.decode(stream))
            while True:
                decode_start = time.perf_counter()
                try:
                    frame = next(frames)
                except StopIteration:
                    break
                if advanced:
                    decode_ms = (time.perf_counter() - decode_start) * 1000
                    stats.add("decode", decode_ms)
                    if control is not None:
                        control.wait_for_slot(frame[3], gop, run_id)
                    frame_start = time.perf_counter()
                    enhanced = enhancements.frame(frame, source_fps, effect, sar, stream_ptr, stats)
                elif control is not None:
                    frame, slot = frame
                    control.wait_for_slot(slot, gop, run_id)
                if (not advanced and control is None and args.start_time and frame.time is not None
                        and frame.time - float((getattr(stream, 'start_time', None) or 0) * stream.time_base) < args.start_time):
                    continue
                if not advanced:
                    from vsr_colour import frame_rgb
                    rgb, maximum = frame_rgb(crop_frame(frame))
                    stats.add("decode", (time.perf_counter() - decode_start) * 1000)
                    with stats.gpu("upload", torch):
                        tensor = torch.from_numpy(rgb).to("cuda:0").permute(2, 0, 1).float().div(maximum).contiguous()
                    with stats.gpu("vsr", torch):
                        result = effect.run(tensor, stream_ptr=stream_ptr)
                        # clone because the VFX SDK retains ownership of the DLPack memory.
                        enhanced = torch.from_dlpack(result.image).clone()
                if encoder is None:
                    with stats.gpu("convert_download", torch):
                        if advanced:
                            from vsr_colour import rgb_to_yuv420
                            out = rgb_to_yuv420(enhanced, bits=10,
                                matrix='bt2020' if args.auto_hdr else 'bt709').cpu().numpy()
                        else:
                            out = (enhanced.clamp(0, 1).mul(255).round().byte()
                                   .permute(1, 2, 0).contiguous().cpu().numpy())
                    with stats.cpu("write"):
                        payload = out.tobytes()
                        assert proc.stdin is not None
                        written = proc.stdin.write(payload)
                        if written != len(payload):
                            raise RuntimeError(f"Incomplete raw-frame pipe write: {written}/{len(payload)} bytes")
                else:
                    with stats.cpu("encode"):
                        payload = encoder.encode(enhanced)
                    with stats.cpu("write"):
                        assert proc.stdin is not None
                        write_all(proc.stdin, payload)
                pipe_bytes += len(payload)
                stats.resolve()
                if advanced:
                    enhancements.observe(decode_ms + (time.perf_counter() - frame_start) * 1000)
                processed += 1
                if processed % 60 == 0:
                    elapsed = time.monotonic() - start_wall
                    print(json.dumps({"event": "progress", "frames": processed,
                                      "run": run_id, "segment": start_segment + processed // gop - 1 if control is not None else None,
                                      "effective_fps": round(processed / max(elapsed, .001), 2),
                                      "enhancement_stride": enhancements.stride if enhancements else 1,
                                      "stages_ms": stats.means(), "pipe_bytes": pipe_bytes,
                                      "pipe_mbps": round(pipe_bytes * 8 / max(elapsed, .001) / 1e6, 3),
                                      "cuda_memory_allocated": torch.cuda.memory_allocated(),
                                      "host_memory": process_resources()}), flush=True)
                if max_frames and processed >= max_frames:
                    break
        assert proc.stdin is not None
        if encoder is not None:
            tail = encoder.flush()
            write_all(proc.stdin, tail)
            pipe_bytes += len(tail)
        proc.stdin.close()
        rc = proc.wait(timeout=90)
        if rc:
            if control is not None and control.cancelled(run_id):
                raise RunCancelled()
            details = "worker stderr" if args.memory_output else str(dest.parent / 'ffmpeg-vsr.log')
            raise RuntimeError(f"FFmpeg exit {rc}; inspect {details}")
        elapsed = time.monotonic() - start_wall
        report = {"status": "encoded", "frames": processed,
                  "elapsed": round(elapsed, 2),
                  "fps": round(processed / max(elapsed, .001), 2),
                  "source": str(source), "output": str(dest),
                  "vsr": args.quality, "nvenc_requested": True, "pipeline": pipeline,
                  "legacy_schedule": "threaded" if threaded else "serial",
                  "generated_frames": enhancements.generated if enhancements else 0,
                  "enhancement_stride": enhancements.stride if enhancements else 1,
                  "stages_ms": stats.means(), "pipe_bytes": pipe_bytes,
                  "pipe_mb_per_second": round(pipe_bytes / max(elapsed, .001) / 1e6, 3),
                  "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                  "worker_cpu_seconds": time.process_time() - cpu_start,
                  "ffmpeg_resources": process_resources(proc), "worker_resources": process_resources()}
        if not args.memory_output:
            (dest.parent / "vsr-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if control is not None and proc is not None:
            control.detach(proc)
        if log_file is not None:
            log_file.close()
        if effect is not None and retained is None:
            effect.close()
        if enhancements is not None and retained is None:
            enhancements.close()
        if encoder is not None:
            encoder.close()
        container.close()


def main() -> int:
    a = parse_args()
    try:
        if a.serve:
            from vsr_server import serve
            serve(a, run)
        else:
            run(a)
        return 0
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as e:
        print(f"VSR WORKER ERROR: {e}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
