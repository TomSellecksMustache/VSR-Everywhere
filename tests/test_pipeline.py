import importlib.util
import json
from fractions import Fraction
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
import vsr_everywhere as worker
from vsr_capabilities import probe_codec
from vsr_metrics import StageMetrics


class ContractChecks(unittest.TestCase):
    def test_legacy_commands_are_exactly_the_preserved_baseline(self):
        cases = json.loads((ROOT / 'tests/fixtures/legacy_commands.json').read_text())
        for case in cases:
            with self.subTest(case=case['kwargs']):
                actual = worker.build_ffmpeg_command(fps=Fraction(*case['fps']), sar=Fraction(*case['sar']), **case['kwargs'])
                self.assertEqual(actual, case['argv'])

    def test_copy_mode_only_muxes_video_and_transcodes_audio(self):
        for segment in ['fmp4', 'mpegts']:
            command = worker.build_ffmpeg_command('ffmpeg', 'input.mp4', 'cache/index.m3u8', 3840, 2160,
                                                 Fraction(24000, 1001), 20, segment, start=125.5,
                                                 encoded_video=True)
            self.assertEqual(command[command.index('-c:v') + 1], 'copy')
            self.assertEqual(command[command.index('-f') + 1], 'mpegts')
            self.assertEqual(command[command.index('-c:a') + 1], 'aac')
            self.assertEqual(command[command.index('-tag:v') + 1], 'hvc1')
            self.assertNotIn('-framerate', command)
            self.assertIn('-shortest', command)
            for flag in ['-vf', '-profile:v', '-pixel_format', '-color_primaries', '-b:v', '-preset']:
                self.assertNotIn(flag, command)
            self.assertLess(command.index('-ss'), command.index('input.mp4'))

    def test_probe_reports_failure_instead_of_inventing_format_support(self):
        with patch('vsr_capabilities.importlib.import_module', side_effect=ImportError('hidden codec wheel')):
            result = probe_codec()
        self.assertFalse(result['importable'])
        self.assertEqual(result['hevc_input_formats'], [])
        self.assertIn('hidden codec wheel', result['reason'])

    def test_nv12_input_declares_the_gpu_conversion_matrix_and_range(self):
        command = worker.build_ffmpeg_command('ffmpeg', 'source.mp4', 'cache/index.m3u8', 3840, 2160,
                                               Fraction(24), 20, 'fmp4', raw_pixel_format='nv12')
        input_args = command[:command.index('-i')]
        for flag, value in [('-colorspace', 'bt709'), ('-color_range', 'tv'),
                            ('-color_primaries', 'bt709'), ('-color_trc', 'bt709')]:
            self.assertEqual(input_args[input_args.index(flag) + 1], value)

    def test_stage_means_count_each_stage_separately(self):
        stats = StageMetrics()
        stats.add('decode', 1)
        stats.add('decode', 3)
        stats.add('write', 7)
        self.assertEqual(stats.means(), {'decode': 2, 'write': 7})
        with patch('vsr_metrics.time.perf_counter', side_effect=[5, 5.25]):
            with stats.cpu('upload'):
                pass
        self.assertEqual(stats.means()['upload'], 250)


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('numpy'), 'NumPy and CPU-capable PyTorch required')
class ColourChecks(unittest.TestCase):
    def test_device_math_matches_independent_numpy_matrices(self):
        import numpy as np
        import torch
        from vsr_colour import rgb_to_yuv420
        rng = np.random.default_rng(42)
        rgb = rng.random((8, 12, 3)).astype(np.float32)
        matrices = {
            'bt709': [[46.5594, 156.6288, 15.8118], [-25.664151, -86.335849, 112], [112, -101.730251, -10.269749]],
            'bt601': [[65.481, 128.553, 24.966], [-37.79684, -74.20316, 112], [112, -93.78602, -18.21398]]
        }
        for matrix, coefficients in matrices.items():
            reference = rgb.astype(np.float64) @ np.array(coefficients).T + np.array([16, 128, 128])
            chroma = reference[..., 1:].reshape(4, 2, 6, 2, 2).mean(axis=(1, 3))
            for bits in [8, 10]:
                with self.subTest(matrix=matrix, bits=bits):
                    surface = rgb_to_yuv420(torch.from_numpy(rgb).permute(2, 0, 1), bits, matrix).numpy()
                    if bits == 10:
                        self.assertTrue(np.all((surface & 63) == 0))
                        surface = surface >> 6
                    scale = 1 << (bits - 8)
                    np.testing.assert_allclose(surface[:8], np.rint(reference[..., 0] * scale), atol=1, rtol=0)
                    np.testing.assert_allclose(surface[8:, 0::2], np.rint(chroma[..., 0] * scale), atol=1, rtol=0)
                    np.testing.assert_allclose(surface[8:, 1::2], np.rint(chroma[..., 1] * scale), atol=1, rtol=0)

    def test_black_white_and_neutral_chroma_use_limited_range(self):
        import torch
        from vsr_colour import rgb_to_yuv420
        for bits in [8, 10]:
            shift = 1 if bits == 8 else 64
            scale = 1 << (bits - 8)
            for level, expected_y in [(0, 16), (1, 235)]:
                output = rgb_to_yuv420(torch.full((3, 4, 4), float(level)), bits)
                self.assertTrue(torch.all(output[:4].to(torch.int32) == expected_y * scale * shift))
                self.assertTrue(torch.all(output[4:].to(torch.int32) == 128 * scale * shift))


if __name__ == '__main__':
    unittest.main()
