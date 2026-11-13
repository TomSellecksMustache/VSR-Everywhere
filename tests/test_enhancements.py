from fractions import Fraction
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from vsr_enhancements import read_exact, unpack_hdr
from vsr_everywhere import build_ffmpeg_command
from vsr_colour import rgb_to_yuv_reference

spec = importlib.util.spec_from_file_location('prepare_veyra', ROOT / 'installer/prepare_veyra.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class EnhancementChecks(unittest.TestCase):
    def test_native_pipe_partial_reads_and_early_eof(self):
        class Fragments:
            def read(self, size):
                return stream.read(min(size, 3))
        stream = io.BytesIO(b'123456789')
        self.assertEqual(read_exact(Fragments(), 9), b'123456789')
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            read_exact(stream, 1)

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

    def test_runtime_extraction_keeps_needed_files_and_rejects_escape(self):
        prefix = 'Veyra-2.1.5-NVIDIA-win64-portable/'
        required = ['avutil-61.dll', 'swscale-10.dll', 'runtime/config/ngx-local.json',
                    'runtime/experimental/nr-ampere/nvngx_dlssnr.dll', 'shaders/ScaleBlit.dxil']
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            archive = folder / 'runtime.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                for name in required + ['Veyra.exe', 'Qt6Core.dll']:
                    package.writestr(prefix + name, b'fixture')
            prepare.extract_runtime(archive, folder / 'private')
            self.assertTrue((folder / 'private/runtime_local').is_dir())
            self.assertFalse((folder / 'private/Veyra.exe').exists())
            self.assertFalse((folder / 'private/Qt6Core.dll').exists())
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr(prefix + 'runtime/../../escape', b'bad')
            with self.assertRaisesRegex(ValueError, 'escapes'):
                prepare.extract_runtime(archive, folder / 'private')
            self.assertFalse((folder / 'escape').exists())


if __name__ == '__main__':
    unittest.main()
