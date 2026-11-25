import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from test_hdr_probe import check_hdr, package_channel, worker_arguments


class ProbeChecks(unittest.TestCase):
    def test_forced_hdr_is_bounded(self):
        arguments = worker_arguments("input file.mkv", "index.m3u8", "ffmpeg", "ffprobe", True)
        self.assertIn("--auto-hdr", arguments)
        self.assertIn("--roku-cbr-five", arguments)
        self.assertEqual(arguments[arguments.index("--seconds") + 1], "32")
        self.assertEqual(arguments[arguments.index("--segment-type") + 1], "mpegts")
        self.assertNotIn("--frame-interpolation", arguments)
        self.assertNotIn("--roku-cbr-five", worker_arguments("a", "b", "c", "d", False))

    def test_ten_bit_sdr_is_not_hdr(self):
        video = {"codec_type": "video", "codec_name": "hevc", "profile": "Main 10",
                 "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084",
                 "color_primaries": "bt2020", "color_space": "bt2020nc"}
        probe = {"streams": [video, {"codec_type": "audio", "codec_name": "aac"}]}
        check_hdr(probe)
        video["color_transfer"] = "bt709"
        with self.assertRaises(ValueError):
            check_hdr(probe)

    def test_channel_package(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "probe.zip"
            package_channel(package, "http://192.0.2.10:18194/index.m3u8")
            with zipfile.ZipFile(package) as archive:
                self.assertIn("manifest", archive.namelist())
                self.assertIn("source/main.brs", archive.namelist())
                ET.fromstring(archive.read("components/HdrProbe.xml"))
                self.assertEqual(json.loads(archive.read("probe.json"))["url"],
                                 "http://192.0.2.10:18194/index.m3u8")
                scene = archive.read("components/HdrProbe.brs").decode()
                self.assertIn('ObserveField("streamingSegment"', scene)
                self.assertIn("segment.hdrModeStr", scene)
                self.assertNotIn("segUrl", scene)


if __name__ == "__main__":
    unittest.main()
