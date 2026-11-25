import contextlib
import importlib.util
import io
import json
from pathlib import Path
from fractions import Fraction
import sys
import types
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
spec = importlib.util.spec_from_file_location(
    "vsr_worker", Path(__file__).resolve().parents[1] / "worker" / "vsr_everywhere.py")
worker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = worker
spec.loader.exec_module(worker)

PRESETS = [(1920, 1080), (2560, 1440), (3840, 2160),
           (5120, 2880), (5760, 3240), (7680, 4320)]


class WorkerChecks(unittest.TestCase):
    def test_processing_geometry_and_delivery_are_independent(self):
        for processing in PRESETS:
            with self.subTest(processing=processing):
                g = worker.geometry(1920, 1080, processing_width=processing[0],
                                    processing_height=processing[1])
                self.assertEqual((g.vsr_width, g.vsr_height), processing)
                for delivery in PRESETS:
                    cmd = worker.build_ffmpeg_command(
                        "ffmpeg", "source.mp4", "cache/index.m3u8",
                        g.vsr_width, g.vsr_height, Fraction(24), 40, "mpegts",
                        stream_width=delivery[0], stream_height=delivery[1])
                    self.assertEqual(cmd[cmd.index("-video_size") + 1],
                                     f"{processing[0]}x{processing[1]}")
                    vf = cmd[cmd.index("-vf") + 1]
                    self.assertIn(f"scale=w='max(2,round(min({delivery[0]},{delivery[1]}*dar)/2)*2)'", vf)
                    self.assertIn(f"h='max(2,round(min({delivery[1]},{delivery[0]}/dar)/2)*2)'", vf)
                    self.assertIn(f"pad={delivery[0]}:{delivery[1]}:", vf)
                    self.assertEqual(cmd[cmd.index("-profile:v") + 1], "main")
                    self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")
                    self.assertIn("title=VSR Everywhere", cmd)

    def test_roku_cbr_keeps_five_second_timing_and_correct_sdr_hdr_formats(self):
        for hdr in (False, True):
            cmd = worker.build_ffmpeg_command("ffmpeg", "source", "cache/index.m3u8", 3840, 2160,
                Fraction(24000, 1001), 40, "mpegts", roku_cbr_five=True, hdr_output=hdr,
                nvenc_cq=1, start_segment=24, start=120.12)
            value = lambda key: cmd[cmd.index(key) + 1]
            self.assertEqual(value("-rc"), "cbr")
            self.assertNotIn("-cq", cmd)
            self.assertEqual([value(key) for key in ("-b:v", "-minrate:v", "-maxrate", "-bufsize")], ["40M"] * 4)
            self.assertEqual(value("-g"), "120")
            self.assertEqual(value("-hls_time"), "4.9")
            self.assertEqual(value("-start_number"), "24")
            self.assertEqual(value("-itsoffset"), "120.120000000")
            self.assertEqual(value("-profile:v"), "main10" if hdr else "main")
            self.assertEqual(value("-pix_fmt"), "p010le" if hdr else "nv12")
            self.assertEqual(value("-color_trc"), "smpte2084" if hdr else "bt709")
        with self.assertRaisesRegex(ValueError, "40 Mbps"):
            worker.build_ffmpeg_command("ffmpeg", "source", "output", 3840, 2160,
                Fraction(24), 20, "mpegts", roku_cbr_five=True)

    def test_widescreen_and_anamorphic_sources_preserve_aspect_ratio(self):
        for w, h, sar in [(1920, 800, Fraction(1)), (720, 480, Fraction(8, 9))]:
            g = worker.geometry(w, h, sar.numerator, sar.denominator, 5120, 2880)
            self.assertLessEqual(g.vsr_width * float(sar), 5120)
            self.assertLessEqual(g.vsr_height, 2880)
            self.assertAlmostEqual(g.vsr_width * float(sar) / g.vsr_height,
                                   w * float(sar) / h, delta=0.002)

    def test_full_and_rolling_hls_keep_existing_audio_and_segment_modes(self):
        for segment_type in ("mpegts", "fmp4"):
            for rolling in (0, 10):
                with self.subTest(segment_type=segment_type, rolling=rolling):
                    cmd = worker.build_ffmpeg_command(
                        "ffmpeg", "source.mp4", "cache/index.m3u8", 5120, 2880,
                        Fraction(24000, 1001), 40, segment_type,
                        stream_width=3840, stream_height=2160,
                        rolling_rewind_minutes=rolling)
                    self.assertEqual(cmd[cmd.index("-hls_segment_type") + 1], segment_type)
                    self.assertEqual(cmd[cmd.index("-hls_list_size") + 1],
                                     "300" if rolling else "0")
                    self.assertEqual("delete_segments" in cmd[cmd.index("-hls_flags") + 1], bool(rolling))
                    self.assertEqual("-hls_playlist_type" in cmd, not bool(rolling))

    def args(self, *extra):
        with patch.object(sys, "argv", ["vsr_everywhere.py", "source.mp4", "--output",
                                       "cache/index.m3u8", *extra]):
            return worker.parse_args()

    def test_8k_delivery_passes_runtime_validation(self):
        # Probe reaches the SDK check without decoding or using a GPU.
        cuda = types.SimpleNamespace(is_available=lambda: True, get_device_name=lambda _: "test GPU")
        modules = {"av": types.ModuleType("av"), "numpy": types.ModuleType("numpy"),
                   "torch": types.SimpleNamespace(cuda=cuda),
                   "nvvfx": types.SimpleNamespace(VideoSuperRes=object)}
        args = self.args("--processing-width", "7680", "--processing-height", "4320",
                         "--stream-width", "7680", "--stream-height", "4320", "--probe")
        output = io.StringIO()
        with patch.dict(sys.modules, modules), contextlib.redirect_stdout(output):
            worker.run(args)
        self.assertTrue(json.loads(output.getvalue())["cuda"])

    def test_invalid_dimensions_are_rejected_before_sdk_loading(self):
        for extra in [("--stream-width", "7682"), ("--stream-height", "4322"),
                      ("--processing-width", "7682"), ("--stream-width", "1921")]:
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "invalid processing/delivery"):
                worker.run(self.args(*extra))


if __name__ == "__main__":
    unittest.main()
