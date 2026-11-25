from fractions import Fraction
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from vsr_enhancements import Enhancements, unpack_hdr
from vsr_everywhere import build_ffmpeg_command
from vsr_colour import rgb_to_yuv_reference


class EnhancementChecks(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('VSR_GPU_TESTS') == '1', 'NVIDIA VFX hardware check')
    def test_interpolation_moves_between_anchors_in_sdr_and_hdr(self):
        import numpy as np
        import torch
        from vsr_metrics import StageMetrics

        rng = np.random.default_rng(7)
        pixels = rng.integers(20, 80, (2160, 3840, 3), dtype=np.uint8)
        pixels[600:1500, 1000:1900] = [170, 130, 90]
        shifted = np.roll(pixels, 80, axis=1)
        def frame(values):
            return SimpleNamespace(format=SimpleNamespace(components=[SimpleNamespace(bits=8)]),
                                   to_ndarray=lambda **kwargs: values)
        left, right = frame(pixels), frame(shifted)
        for hdr in (False, True):
            with self.subTest(hdr=hdr):
                state = Enhancements(SimpleNamespace(auto_hdr=hdr, frame_interpolation=True,
                    stream_width=3840, stream_height=2160), 24)
                try:
                    stats = StageMetrics()
                    previous = state.render(left, 0, None, 1, 0, stats)
                    current = state.render(right, 2, None, 1, 0, stats)
                    middle = state.frame((left, right, Fraction(1, 2), 1, 0, 2), 24, None, 1, 0, stats)
                    if hdr:
                        previous, current = unpack_hdr(previous), unpack_hdr(current)
                    self.assertGreater((middle - previous).abs().mean().item(), .001)
                    self.assertGreater((middle - current).abs().mean().item(), .001)
                    self.assertEqual(state.generated, 1)
                finally:
                    state.close()
                    torch.cuda.empty_cache()

    def test_hdr_signaling_for_gpu_copy_and_lookahead_paths(self):
        for copy in (False, True):
            command = build_ffmpeg_command('ffmpeg', 'source', 'output', 3840, 2160,
                Fraction(60), 20, 'mpegts', encoded_video=copy, raw_pixel_format='p010le',
                hdr_output=True, nvenc_lookahead=12)
            if copy:
                self.assertIn('colour_primaries=9:transfer_characteristics=16:matrix_coefficients=9', command[command.index('-bsf:v') + 1])
            else:
                self.assertEqual(command.count('smpte2084'), 2)
                self.assertEqual(command[command.index('-pixel_format') + 1], 'p010le')
                self.assertIn('-rc-lookahead', command)
    def test_sdr_gpu_conversion_preserves_bt709_without_hdr_tags(self):
        command = build_ffmpeg_command('ffmpeg', 'source', 'output', 3840, 2160,
            Fraction(24000, 1001), 40, 'mpegts', raw_pixel_format='p010le')
        self.assertEqual(command[command.index('-pixel_format') + 1], 'p010le')
        self.assertEqual(command.count('bt709'), 6)
        self.assertNotIn('smpte2084', command)
        self.assertEqual(command[command.index('-framerate') + 1], '24000/1001')
        self.assertEqual(command[command.index('-maxrate') + 1], '40M')

    def test_bt2020_reference_levels_and_packed_hdr_channels(self):
        import numpy as np
        values = rgb_to_yuv_reference([[0, 0, 0], [1, 1, 1], [1, 0, 0]], bits=10, matrix='bt2020')
        np.testing.assert_allclose(values[:2], [[64, 512, 512], [940, 512, 512]])
        self.assertAlmostEqual(values[2, 0], 64 + 876 * .2627)
        if importlib.util.find_spec('torch'):
            import torch
            packed = torch.tensor([[0xC00003FF, 0xC00FFC00, 0xFFF00000]], dtype=torch.uint32)
            np.testing.assert_allclose(unpack_hdr(packed).numpy()[:, 0, :], np.eye(3))


if __name__ == '__main__':
    unittest.main()
