"""Conservative source-bar inspection; all decoding is bounded, local and CPU-only."""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import subprocess
import time

_CROP = re.compile(r"\bcrop=(\d+):(\d+):(\d+):(\d+)\b")


class FrameCrop:
    """Crop native YUV frames before any preview or enhancement conversion."""
    def __init__(self, rectangle, source_width, source_height):
        if isinstance(rectangle, str):
            try:
                rectangle = tuple(int(value) for value in rectangle.split(":"))
            except ValueError:
                raise ValueError("Invalid source crop") from None
        full = (0, 0, source_width, source_height)
        self.rectangle = None if rectangle is None or rectangle == full else rectangle
        if self.rectangle is not None:
            if len(self.rectangle) != 4:
                raise ValueError("Invalid source crop")
            x, y, width, height = self.rectangle
            if (min(x, y) < 0 or min(width, height) < 16 or any(value % 2 for value in self.rectangle)
                    or x + width > source_width or y + height > source_height):
                raise ValueError("Invalid source crop")
        self.width, self.height = self.rectangle[2:] if self.rectangle else full[2:]
        self.graph = None

    def __call__(self, frame):
        if self.rectangle is None:
            return frame
        if self.graph is None:
            import av
            x, y, width, height = self.rectangle
            self.graph = av.filter.Graph()
            source = self.graph.add_buffer(template=frame)
            crop = self.graph.add("crop", f"{width}:{height}:{x}:{y}:exact=1")
            sink = self.graph.add("buffersink")
            source.link_to(crop)
            crop.link_to(sink)
            self.graph.configure()
        self.graph.push(frame)
        return self.graph.pull()


def fit_size(width, height, sar, max_width, max_height):
    """Fit display aspect inside even bounds, rounding to the nearest even pixel."""
    sar = Fraction(sar)
    if min(width, height) <= 0 or min(max_width, max_height) < 16 or sar <= 0:
        raise ValueError("Invalid crop or output geometry")
    max_width, max_height = max_width // 2 * 2, max_height // 2 * 2
    ratio = Fraction(width, height) * sar
    fitted_height = min(Fraction(max_height), Fraction(max_width) / ratio)
    size = (round(fitted_height * ratio / 2) * 2, round(fitted_height / 2) * 2)
    if min(size) < 16:
        raise ValueError("Source aspect cannot fit the output bounds")
    return size


def choose_crop(candidates, source_width, source_height):
    """Union (x, y, width, height) reports; None marks an uncertain window."""
    full = (0, 0, source_width, source_height)
    valid = []
    for rectangle in candidates:
        if rectangle is None:
            return full
        x, y, width, height = rectangle
        if width * 2 < source_width or height * 2 < source_height:
            continue
        margins = (x, y, source_width - x - width, source_height - y - height)
        if any(margin < 0 or margin % 2 for margin in margins) or rectangle == full:
            return full
        valid.append(rectangle)
    if not valid:
        return full
    x, y = min(rect[0] for rect in valid), min(rect[1] for rect in valid)
    right = max(rect[0] + rect[2] for rect in valid)
    bottom = max(rect[1] + rect[3] for rect in valid)
    return x, y, right - x, bottom - y


def _run(command, deadline, timeout=2):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Crop inspection timed out")
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True, timeout=min(timeout, remaining),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def inspect(source, ffmpeg, ffprobe, max_width, max_height):
    # Reserve cleanup time within the six-second wall-clock budget.
    deadline = time.monotonic() + 5.5
    try:
        path = Path(source)
        if path.anchor.startswith("\\\\"):
            raise ValueError("A local regular file is required")
        path = path.resolve(strict=True)
        if path.anchor.startswith("\\\\") or not path.is_file():
            raise ValueError("A local regular file is required")
        probe = _run([str(ffprobe), "-v", "error", "-protocol_whitelist", "file",
                      "-select_streams", "v:0", "-show_entries",
                      "stream=width,height,sample_aspect_ratio,duration:format=duration",
                      "-of", "json", str(path)], deadline)
        data = json.loads(probe.stdout)
        video = data["streams"][0]
        source_width, source_height = int(video["width"]), int(video["height"])
        if not (0 < source_width <= 1920 and 0 < source_height <= 1080):
            raise ValueError("Unsupported source dimensions")
    except (OSError, subprocess.SubprocessError, TimeoutError, ValueError, TypeError, OverflowError, KeyError, IndexError):
        raise RuntimeError("Cannot probe local source video geometry") from None
    try:
        sar = Fraction(str(video.get("sample_aspect_ratio", "1:1")).replace(":", "/"))
        if sar <= 0:
            raise ValueError("Invalid source SAR")
    except (ValueError, ZeroDivisionError):
        sar = Fraction(1)
    stream_width, stream_height = fit_size(source_width, source_height, sar, max_width, max_height)
    result = dict(source_width=source_width, source_height=source_height,
                  x=0, y=0, width=source_width, height=source_height,
                  sar_num=sar.numerator, sar_den=sar.denominator,
                  stream_width=stream_width, stream_height=stream_height, detected=False)
    try:
        duration = float(video.get("duration") or data.get("format", {}).get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            return result
        short = duration < 3
        # shortcut: sample three points, so uncertain or changing-aspect titles can retain bars.
        offsets = (0,) if short else tuple(duration * fraction for fraction in (.10, .45, .80))
        windows = []
        for offset in offsets:
            # Normalize the threshold so limited-range black also works at 10 bits.
            filters = (f"fps={8 / duration:.9g}," if short else "") + "cropdetect=limit=0.078431372549:round=2:reset=0"
            output = _run([str(ffmpeg), "-hide_banner", "-nostdin", "-nostats",
                           "-loglevel", "info", "-protocol_whitelist", "file",
                           "-hwaccel", "none", "-threads", "1", "-ss", f"{offset:.6f}",
                           "-noautorotate", "-i", str(path), "-map", "0:v:0",
                           "-an", "-sn", "-dn", "-filter_threads", "1", "-vf", filters,
                           "-frames:v", "8", "-f", "null", "-"], deadline)
            reports = [(int(x), int(y), int(width), int(height))
                       for width, height, x, y in _CROP.findall(output.stderr)]
            if len(reports) < 2 or reports[-1] != reports[-2]:
                return result
            window = choose_crop(reports, source_width, source_height)
            if window == (0, 0, source_width, source_height):
                return result
            windows.append(window)
        x, y, width, height = choose_crop(windows, source_width, source_height)
        stream_width, stream_height = fit_size(width, height, sar, max_width, max_height)
        result.update(x=x, y=y, width=width, height=height,
                      stream_width=stream_width, stream_height=stream_height,
                      detected=(width, height) != (source_width, source_height))
    except (OSError, subprocess.SubprocessError, TimeoutError, ValueError, OverflowError):
        pass
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--max-width", required=True, type=int)
    parser.add_argument("--max-height", required=True, type=int)
    args = parser.parse_args()
    try:
        result = inspect(args.source, args.ffmpeg, args.ffprobe, args.max_width, args.max_height)
    except (RuntimeError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
