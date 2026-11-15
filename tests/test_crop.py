"""Conservative bar detection and native-frame cropping checks; no GPU required."""
from fractions import Fraction
import importlib.util
from itertools import product
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from vsr_crop import FrameCrop, choose_crop, fit_size, inspect


class CropGeometryChecks(unittest.TestCase):
    def test_crop_union_keeps_every_supported_picture_edge(self):
        self.assertEqual(choose_crop([(6, 60, 628, 360), (2, 58, 636, 364)],
                                     640, 480), (2, 58, 636, 364))
        self.assertEqual(choose_crop([(0, 180, 640, 120), (0, 60, 640, 360)],
                                     640, 480), (0, 60, 640, 360))

    def test_uncertain_full_frame_or_severe_only_reports_keep_the_source(self):
        for candidates in ([], [None], [(0, 60, 640, 360), None],
                           [(0, 60, 640, 360), (0, 0, 640, 480)],
                           [(0, 180, 640, 120)], [(0, 61, 640, 358)],
                           [(-2, 60, 640, 360)], [(0, 60, 642, 360)]):
            with self.subTest(candidates=candidates):
                self.assertEqual(choose_crop(candidates, 640, 480), (0, 0, 640, 480))

    def test_known_source_display_aspects_fit_even_delivery_bounds(self):
        for width, height, sar, maximum, expected in (
                (716, 480, Fraction(1), (3840, 2160), (3222, 2160)),
                (640, 360, Fraction(1), (3840, 2160), (3840, 2160)),
                (720, 480, Fraction(8, 9), (3840, 2160), (2880, 2160)),
                (1920, 800, Fraction(1), (3840, 2160), (3840, 1600)),
                (640, 480, Fraction(1), (640, 360), (480, 360)),
                (360, 640, Fraction(1), (3840, 2160), (1216, 2160)),
                (640, 360, Fraction(1), (641, 361), (640, 360))):
            with self.subTest(width=width, height=height, sar=sar, maximum=maximum):
                actual = fit_size(width, height, sar, *maximum)
                self.assertEqual(actual, expected)
                self.assertTrue(all(size >= 16 and size % 2 == 0 for size in actual))
                self.assertTrue(all(size <= bound for size, bound in zip(actual, maximum)))
                self.assertLessEqual(abs(Fraction(actual[0], actual[1]) -
                                         Fraction(width, height) * sar), Fraction(1, actual[1]))

    def test_invalid_or_unrepresentable_delivery_geometry_is_rejected(self):
        for arguments in ((0, 480, 1, 3840, 2160), (640, 480, 0, 3840, 2160),
                          (640, 480, 1, 15, 2160), (1920, 1, 1, 640, 360)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                fit_size(*arguments)

    def test_frame_crop_rejects_bounds_odd_coordinates_and_small_dimensions(self):
        for rectangle in ((-2, 60, 640, 360), (2, 60, 640, 360), (0, 122, 640, 360),
                          (1, 60, 638, 360), (0, 61, 640, 358), (0, 60, 639, 360),
                          (0, 60, 640, 359), (0, 60, 14, 360), (0, 60, 640, 14),
                          (0, 60, 640), "0:no:640:360"):
            with self.subTest(rectangle=rectangle), self.assertRaises(ValueError):
                FrameCrop(rectangle, 640, 480)


@unittest.skipUnless(importlib.util.find_spec("av") and importlib.util.find_spec("numpy"),
                     "PyAV and NumPy required")
class NativeFrameCropChecks(unittest.TestCase):
    def test_native_planes_and_metadata_survive_multiple_frames_at_both_depths(self):
        import av
        import numpy as np
        for bits, pixel_format in ((8, "yuv420p"), (10, "yuv420p10le")):
            with self.subTest(bits=bits):
                crop = FrameCrop((0, 60, 640, 360), 640, 480)
                retained = []
                dtype = np.dtype(np.uint16 if bits == 10 else np.uint8)
                for slot in range(3):
                    frame = av.VideoFrame(640, 480, pixel_format)
                    expected = []
                    for index, plane in enumerate(frame.planes):
                        rows = np.arange(plane.height, dtype=np.uint32)[:, None]
                        columns = np.arange(plane.line_size // dtype.itemsize, dtype=np.uint32)
                        samples = ((rows * 7 + columns * 3 + index * 41 + slot * 17)
                                   % (1 << bits)).astype(dtype)
                        plane.update(samples.tobytes())
                        top, height = (60, 360) if index == 0 else (30, 180)
                        expected.append(samples[top:top + height, :plane.width].copy())
                    frame.pts, frame.time_base = (12 + slot) * 1001, Fraction(1, 24000)
                    frame.color_range, frame.colorspace = 1, 6
                    frame.color_trc, frame.color_primaries = 1, 6
                    original = tuple(bytes(plane) for plane in frame.planes)
                    metadata = (frame.pts, frame.time_base, frame.color_range, frame.colorspace,
                                frame.color_trc, frame.color_primaries)
                    output = crop(frame)
                    retained.append((frame, output, original, expected, metadata))
                for frame, output, original, expected, metadata in retained:
                    self.assertEqual((output.width, output.height, output.format.name),
                                     (640, 360, pixel_format))
                    self.assertEqual((frame.width, frame.height), (640, 480))
                    self.assertEqual(tuple(bytes(plane) for plane in frame.planes), original)
                    for item in (frame, output):
                        self.assertEqual((item.pts, item.time_base, item.color_range, item.colorspace,
                                          item.color_trc, item.color_primaries), metadata)
                    for plane, samples in zip(output.planes, expected):
                        actual = np.frombuffer(plane, dtype=dtype).reshape(
                            plane.height, plane.line_size // dtype.itemsize)[:, :plane.width]
                        np.testing.assert_array_equal(actual, samples)

    def test_no_crop_returns_the_original_frame(self):
        import av
        frame = av.VideoFrame(640, 480, "yuv420p")
        for rectangle in (None, (0, 0, 640, 480), "0:0:640:480"):
            with self.subTest(rectangle=rectangle):
                self.assertIs(FrameCrop(rectangle, 640, 480)(frame), frame)

    def test_crop_string_uses_the_same_native_rectangle(self):
        import av
        frame = av.VideoFrame(640, 480, "yuv420p10le")
        frame.time_base = Fraction(1, 24000)
        output = FrameCrop("0:60:640:360", 640, 480)(frame)
        self.assertEqual((output.width, output.height, output.format.name),
                         (640, 360, "yuv420p10le"))


@unittest.skipUnless(os.environ.get("VSR_FFMPEG") and os.environ.get("VSR_FFPROBE"),
                     "Set VSR_FFMPEG and VSR_FFPROBE for CPU media checks")
class CropInspectionChecks(unittest.TestCase):
    def make_clip(self, folder, name, picture, duration, pixel_format="yuv420p"):
        target = Path(folder) / name
        command = [os.environ["VSR_FFMPEG"], "-hide_banner", "-nostdin", "-loglevel", "error",
                   "-f", "lavfi", "-i", picture, "-t", str(duration), "-an",
                   "-threads", "1", "-filter_threads", "1", "-c:v", "ffv1",
                   "-pix_fmt", pixel_format, "-y", str(target)]
        result = subprocess.run(command, capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        probe = subprocess.run([os.environ["VSR_FFPROBE"], "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(target)],
                               capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(probe.returncode, 0, probe.stderr.decode(errors="replace"))
        self.assertEqual(probe.stdout.decode().strip(), pixel_format)
        return target

    def test_stable_bars_are_detected_in_short_and_separated_window_clips(self):
        with tempfile.TemporaryDirectory() as folder:
            for pixel_format, duration in product(("yuv420p", "yuv420p10le"), (2, 4)):
                with self.subTest(pixel_format=pixel_format, duration=duration):
                    target = self.make_clip(folder, f"bars-{pixel_format}-{duration}.mkv",
                        "color=c=gray:s=640x360:r=24,pad=640:480:0:60:color=black", duration,
                        pixel_format)
                    result = inspect(target, os.environ["VSR_FFMPEG"],
                                     os.environ["VSR_FFPROBE"], 3840, 2160)
                    self.assertTrue(result["detected"])
                    self.assertEqual(tuple(result[key] for key in ("x", "y", "width", "height")),
                                     (0, 60, 640, 360))
                    self.assertEqual((result["source_width"], result["source_height"]), (640, 480))
                    self.assertEqual((result["sar_num"], result["sar_den"]), (1, 1))
                    self.assertEqual((result["stream_width"], result["stream_height"]), (3840, 2160))

    def test_full_dark_or_full_picture_frames_are_not_cropped(self):
        with tempfile.TemporaryDirectory() as folder:
            for pixel_format, colour in product(("yuv420p", "yuv420p10le"), ("black", "gray")):
                with self.subTest(pixel_format=pixel_format, colour=colour):
                    target = self.make_clip(folder, f"{colour}-{pixel_format}.mkv",
                                            f"color=c={colour}:s=640x480:r=24", 4, pixel_format)
                    result = inspect(target, os.environ["VSR_FFMPEG"],
                                     os.environ["VSR_FFPROBE"], 3840, 2160)
                    self.assertFalse(result["detected"])
                    self.assertEqual(tuple(result[key] for key in ("x", "y", "width", "height")),
                                     (0, 0, 640, 480))
                    self.assertEqual((result["stream_width"], result["stream_height"]), (2880, 2160))


if __name__ == "__main__":
    unittest.main()
