"""Repeatable worker benchmark. Run with the same CUDA Python as the worker."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--seconds", type=float, default=12)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--pipeline", choices=["gpu", "legacy"], default="legacy")
    parser.add_argument("--worker", type=Path, default=Path(__file__).resolve().parents[1] / "worker/vsr_everywhere.py")
    parser.add_argument("--unmodified", action="store_true", help="Benchmark the preserved worker without new flags")
    parser.add_argument("--legacy-schedule", choices=["serial", "threaded"], default="serial")
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--segment-type", choices=["mpegts", "fmp4"], default="mpegts")
    parser.add_argument("--quality", default="STREAMING_MEDIUM")
    parser.add_argument("--preset", default="p4")
    parser.add_argument("--start-time", type=float, default=0)
    parser.add_argument("--processing-width", type=int, default=3840)
    parser.add_argument("--processing-height", type=int, default=2160)
    parser.add_argument("--stream-width", type=int, default=3840)
    parser.add_argument("--stream-height", type=int, default=2160)
    args = parser.parse_args()
    if args.repeat < 1 or args.seconds <= 0:
        parser.error("repeat and seconds must be positive")
    args.out.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run([args.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(args.source.resolve())],
                           capture_output=True, text=True, check=True)
    metadata = json.loads(probe.stdout)
    (args.out / "source.ffprobe.json").write_text(probe.stdout, encoding="utf-8")
    summaries = []
    hidden = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for run in range(args.repeat):
        folder = args.out / f"run-{run + 1:02}"
        folder.mkdir(exist_ok=False)
        output = folder / "index.m3u8"
        command = [sys.executable, str(args.worker.resolve()), str(args.source.resolve()), "--output", str(output.resolve()),
                   "--hls", "--seconds", str(args.seconds), "--start-time", str(args.start_time),
                   "--ffmpeg", str(Path(args.ffmpeg).resolve()), "--ffprobe", str(Path(args.ffprobe).resolve()),
                   "--video-mbps", "20", "--segment-type", args.segment_type, "--quality", args.quality,
                   "--nvenc-preset", args.preset, "--processing-width", str(args.processing_width),
                   "--processing-height", str(args.processing_height), "--stream-width", str(args.stream_width),
                   "--stream-height", str(args.stream_height)]
        if not args.unmodified:
            command += ["--pipeline", args.pipeline]
            if args.legacy_schedule != "serial":
                command += ["--legacy-schedule", args.legacy_schedule]
        started = time.perf_counter()
        done = threading.Event()
        samples = []

        def sample_gpu():
            while not done.is_set():
                try:
                    measurement = subprocess.run(["nvidia-smi", "--id=0", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
                                                 text=True, capture_output=True, timeout=5, creationflags=hidden)
                    if measurement.returncode == 0:
                        memory, usage = measurement.stdout.strip().split(",")
                        samples.append({"time": round(time.perf_counter() - started, 4),
                                        "gpu_used_mb": float(memory), "gpu_utilization_percent": float(usage)})
                except (OSError, subprocess.SubprocessError, ValueError):
                    pass
                done.wait(1)

        sampler = threading.Thread(target=sample_gpu, daemon=True)
        sampler.start()
        events = []
        try:
            with (folder / "worker.stderr.log").open("w", encoding="utf-8") as error_log, \
                    (folder / "events.jsonl").open("w", encoding="utf-8") as event_log:
                with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error_log, text=True, bufsize=1, creationflags=hidden) as process:
                    for line in process.stdout:
                        event_log.write(line)
                        try:
                            event = json.loads(line)
                            event["observed_at_s"] = time.perf_counter() - started
                            events.append(event)
                        except ValueError:
                            continue
                    code = process.wait()
                    if code:
                        raise RuntimeError(f"Worker exited {code}; inspect {folder / 'worker.stderr.log'}")
        finally:
            done.set()
            sampler.join(timeout=7)
        report = next((event for event in reversed(events) if event.get("status") == "encoded"), None)
        if report is None:
            raise RuntimeError("Worker did not publish an encoded report")
        progress = [e for e in events if e.get("event") == "progress"]
        sustained = None
        if len(progress) >= 2:
            sustained = (progress[-1]["frames"] - progress[0]["frames"]) / (progress[-1]["observed_at_s"] - progress[0]["observed_at_s"])
        resources = report.get("ffmpeg_resources", {})
        cpu = report.get("worker_cpu_seconds")
        summary = {"run": run + 1, "frames": report["frames"], "reported_fps": report["fps"],
                   "sustained_fps": sustained, "wall_seconds": time.perf_counter() - started,
                   "stages_ms": report.get("stages_ms"), "pipeline": report.get("pipeline", "original"),
                   "legacy_schedule": report.get("legacy_schedule", "serial"),
                   "pipe_mb_per_second": report.get("pipe_mb_per_second"),
                   "cuda_peak_allocated_bytes": report.get("cuda_peak_allocated_bytes"),
                   "worker_resources": report.get("worker_resources"), "ffmpeg_resources": resources,
                   "combined_cpu_percent_one_core": ((cpu + resources.get("cpu_seconds", 0)) / report["elapsed"] * 100) if cpu is not None else None,
                   "gpu_peak_used_mb": max((s["gpu_used_mb"] for s in samples), default=None)}
        summaries.append(summary)
        (folder / "gpu-samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
        valid = subprocess.run([args.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
                               capture_output=True, text=True, check=True)
        (folder / "output.ffprobe.json").write_text(valid.stdout, encoding="utf-8")
        print(json.dumps(summary), flush=True)
    result = {"source": str(args.source), "source_fps": next(s["avg_frame_rate"] for s in metadata["streams"] if s["codec_type"] == "video"),
              "cpu_count": os.cpu_count(), "runs": summaries,
              "median_sustained_fps": statistics.median(s["sustained_fps"] for s in summaries if s["sustained_fps"] is not None)}
    (args.out / "benchmark.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"benchmark": str(args.out / "benchmark.json"), "median_sustained_fps": result["median_sustained_fps"]}), flush=True)


if __name__ == "__main__":
    main()
