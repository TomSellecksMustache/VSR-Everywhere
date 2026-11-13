"""Compare existing outputs from the same source interval; no pass/fail threshold."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys


def parse_metrics(stderr):
    metrics = {}
    for name, required in [("PSNR", {"average", "min", "max"}), ("SSIM", {"All"})]:
        summaries = re.findall(rf"\b{name} ([^\r\n]+)", stderr)
        if not summaries:
            raise ValueError(f"FFmpeg did not report {name}; inspect compare.stderr.log")
        values = {}
        for key, raw in re.findall(r"(\w+):([^\s]+)", summaries[-1]):
            value = float(raw)
            if math.isnan(value):
                raise ValueError(f"FFmpeg reported NaN {name}; inputs may have no comparable frames")
            values[key] = value if math.isfinite(value) else raw.lower()
        if not required <= values.keys():
            raise ValueError(f"Incomplete {name} summary; inspect compare.stderr.log")
        metrics[name.lower()] = values
    return metrics


def run_ffmpeg(command, out, name):
    (out / f"{name}.command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
    log = out / f"{name}.stderr.log"
    with (out / f"{name}.stdout.log").open("wb") as stdout, log.open("wb") as stderr:
        result = subprocess.run(command, cwd=out, stdout=stdout, stderr=stderr,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(f"FFmpeg exited {result.returncode}; inspect {log}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy", type=Path, help="Existing legacy HLS playlist or MP4")
    parser.add_argument("candidate", type=Path, help="Existing candidate of the same interval and frame rate")
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--out", type=Path, required=True, help="New directory for logs and summary")
    parser.add_argument("--stills", action="store_true", help="Export the first frame of each input, at most 960 pixels wide")
    args = parser.parse_args(argv)
    legacy, candidate, out = args.legacy.resolve(), args.candidate.resolve(), args.out.resolve()
    for source in (legacy, candidate):
        if not source.is_file():
            parser.error(f"Input does not exist: {source}")
    # Resolve executable paths before changing the child process directory.
    ffmpeg = str(Path(args.ffmpeg).resolve()) if Path(args.ffmpeg).is_file() else args.ffmpeg
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        parser.error(f"Cannot create a new output directory: {exc}")
    summary = {
        "legacy": str(legacy), "candidate": str(candidate),
        "scope": "First video streams, PTS reset to zero, common time base, stop at shorter input; no pass/fail threshold.",
        "requires": "Inputs must cover the same source interval and frame rate; review stream metadata in compare.stderr.log.",
    }
    graph = (
        "[0:v:0]settb=AVTB,setpts=PTS-STARTPTS,split=2[lp][ls];"
        "[1:v:0]settb=AVTB,setpts=PTS-STARTPTS,split=2[cp][cs];"
        "[cp][lp]psnr=stats_file=psnr.frames.log:shortest=1:repeatlast=0[p];"
        "[cs][ls]ssim=stats_file=ssim.frames.log:shortest=1:repeatlast=0[s]"
    )
    try:
        command = [ffmpeg, "-hide_banner", "-nostdin", "-hwaccel", "none", "-i", str(legacy),
                   "-hwaccel", "none", "-i", str(candidate), "-filter_complex", graph,
                   "-map", "[p]", "-map", "[s]", "-an", "-f", "null", "-"]
        run_ffmpeg(command, out, "compare")
        summary.update(parse_metrics((out / "compare.stderr.log").read_text(encoding="utf-8", errors="replace")))
        counts = [sum(line.startswith("n:") for line in (out / f"{metric}.frames.log").read_text().splitlines())
                  for metric in ("psnr", "ssim")]
        if not counts[0] or counts[0] != counts[1]:
            raise ValueError(f"Invalid compared frame counts: PSNR={counts[0]}, SSIM={counts[1]}")
        summary["frames_compared"] = counts[0]
        if args.stills:
            summary["stills"] = []
            for name, source in [("legacy", legacy), ("candidate", candidate)]:
                command = [ffmpeg, "-hide_banner", "-nostdin", "-hwaccel", "none", "-i", str(source),
                           "-map", "0:v:0", "-vf", "scale=w='min(960,iw)':h=-2", "-frames:v", "1",
                           "-an", "-update", "1", str(out / f"{name}.png")]
                run_ffmpeg(command, out, f"{name}-still")
                summary["stills"].append(f"{name}.png")
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        summary["error"] = str(exc)
    (out / "colour-comparison.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(summary, allow_nan=False), flush=True)
    return 1 if "error" in summary else 0


if __name__ == "__main__":
    sys.exit(main())
