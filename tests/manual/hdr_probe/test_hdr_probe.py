"""Force the installed Auto HDR worker on for a 32-second HDR playback probe."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def worker_arguments(source, output, ffmpeg, ffprobe, roku_cbr):
    arguments = [
        str(source), "--output", str(output), "--hls", "--auto-hdr",
        "--seconds", "32", "--pipeline", "legacy", "--segment-type", "mpegts",
        "--ffmpeg", str(ffmpeg), "--ffprobe", str(ffprobe), "--video-mbps", "40",
        "--processing-width", "3840", "--processing-height", "2160",
        "--stream-width", "3840", "--stream-height", "2160",
        "--quality", "ULTRA", "--strength", "1", "--nvenc-preset", "p7",
        "--nvenc-tune", "hq", "--nvenc-lookahead", "10", "--nvenc-spatial-aq",
    ]
    if roku_cbr:
        arguments.append("--roku-cbr-five")
    return arguments


def check_hdr(probe):
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    expected = {"codec_name": "hevc", "profile": "Main 10", "color_transfer": "smpte2084",
                "color_primaries": "bt2020", "color_space": "bt2020nc"}
    for name, value in expected.items():
        if video.get(name) != value:
            raise ValueError(f"Not the expected HDR10 candidate: {name}={video.get(name)!r}")
    if video.get("pix_fmt") not in {"yuv420p10le", "p010le"}:
        raise ValueError("Expected 10-bit 4:2:0 video")
    if not any(stream.get("codec_name") == "aac" for stream in probe["streams"]):
        raise ValueError("Use a source with audio; the probe expects AAC audio too")


def package_channel(destination, url):
    app = Path(__file__).with_name("app")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(app.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(app).as_posix())
        archive.writestr("probe.json", json.dumps({"url": url}))


class HlsHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="An SDR file at 1080p or below, with audio")
    parser.add_argument("--host", type=ipaddress.IPv4Address, required=True, help="Server LAN address reachable from Roku")
    parser.add_argument("--port", type=int, default=18194)
    parser.add_argument("--package-only", action="store_true", help="Build ZIP without encoding or serving")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.host.is_unspecified or args.host.is_multicast:
        parser.error("Use the server's IPv4 address and a port from 1 through 65535")
    if not args.package_only and (args.source is None or not args.source.is_file()):
        parser.error("--source must name an existing SDR media file")
    root = Path(__file__).resolve().parents[3] / "dist" / "hdr-test"
    root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="probe-", dir=root))
    channel = output / "hdr-probe.zip"
    url = f"http://{args.host}:{args.port}/index.m3u8"
    package_channel(channel, url)
    print(f"Channel ZIP: {channel}", flush=True)
    if args.package_only:
        return
    data = Path(os.environ["LOCALAPPDATA"]) / "Jellyfin"
    configuration = ET.parse(data / "plugins/configurations/Jellyfin.Plugin.VsrEverywhere.xml").getroot()
    python, ffmpeg, ffprobe = (configuration.findtext(name) for name in ("PythonPath", "FfmpegPath", "FfprobePath"))
    plugin = data / "plugins/VSR-Everywhere"
    worker = plugin / "worker/vsr_everywhere.py"
    if not all(value and Path(value).is_file() for value in (python, ffmpeg, ffprobe)) or not worker.is_file():
        raise ValueError("Installed plugin/runtime paths are unavailable")
    public = output / "stream"
    public.mkdir()
    command = [python, str(worker), *worker_arguments(args.source.resolve(), public / "index.m3u8",
               ffmpeg, ffprobe, "--roku-cbr-five" in worker.read_text(encoding="utf-8"))]
    print("Encoding 32 seconds through the installed worker with Auto HDR forced on.", flush=True)
    subprocess.run(command, cwd=plugin, check=True)
    first = next(iter(sorted(public.glob("seg_*.ts"))), None)
    if first is None:
        raise ValueError("Worker did not generate MPEG-TS segments")
    probe = json.loads(subprocess.check_output(
        [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(first)], encoding="utf-8"))
    (output / "encoding.json").write_text(json.dumps(probe, indent=2), encoding="utf-8")
    check_hdr(probe)
    for name in ("ffmpeg-vsr.log", "vsr-result.json"):
        if (public / name).exists():
            shutil.move(public / name, output / name)
    print(f"PASS: HEVC Main 10, 10-bit, BT.2020/PQ, AAC. Playback URL: {url}", flush=True)
    print("Open the HLS URL in a compatible player, or sideload the optional developer-channel ZIP.", flush=True)
    print("Check HDRTEST console lines and the TV's HDR picture mode. Ctrl+C stops this server.", flush=True)
    with ThreadingHTTPServer((str(args.host), args.port), partial(HlsHandler, directory=str(public))) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
