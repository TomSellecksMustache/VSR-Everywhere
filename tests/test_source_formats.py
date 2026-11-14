import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
from vsr_colour import frame_rgb


@unittest.skipUnless(importlib.util.find_spec('av') and importlib.util.find_spec('numpy'), 'PyAV and NumPy required')
class SourceFormatChecks(unittest.TestCase):
    def test_720p_and_1080p_sdr_at_both_bit_depths(self):
        import av
        import numpy as np
        for width, height in ((1280, 720), (1920, 1080)):
            for bits in (8, 10):
                with self.subTest(width=width, bits=bits):
                    frame = av.VideoFrame(width, height, 'yuv420p10le' if bits == 10 else 'yuv420p')
                    for index, plane in enumerate(frame.planes):
                        dtype = np.uint16 if bits == 10 else np.uint8
                        samples = np.full((plane.height, plane.line_size // (2 if bits == 10 else 1)), 512 if bits == 10 else 128, dtype=dtype)
                        if index == 0:
                            ramp = np.arange(plane.width) % (876 if bits == 10 else 219) + (64 if bits == 10 else 16)
                            samples[:, :plane.width] = ramp.astype(dtype)
                        plane.update(samples.tobytes())
                    rgb, maximum = frame_rgb(frame)
                    self.assertEqual(rgb.shape, (height, width, 3))
                    self.assertTrue(rgb.flags.c_contiguous)
                    self.assertEqual(maximum, 65535 if bits == 10 else 255)
                    self.assertEqual(rgb.dtype, np.uint16 if bits == 10 else np.uint8)
                    self.assertGreaterEqual(rgb.min(), 0)
                    self.assertLessEqual(rgb.max(), maximum)
                    if bits == 10:
                        self.assertGreater(len(np.unique(rgb[0, :, 0])), 256, '10-bit input was reduced to 8-bit before processing')

    def test_rejects_higher_depth_before_conversion(self):
        frame = SimpleNamespace(format=SimpleNamespace(components=[SimpleNamespace(bits=12)]))
        with self.assertRaisesRegex(ValueError, '10-bit SDR'):
            frame_rgb(frame)
