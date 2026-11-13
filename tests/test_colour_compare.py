import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from compare_vsr_colour import main, parse_metrics, run_ffmpeg


class ColourComparisonChecks(unittest.TestCase):
    def test_summary_parser_preserves_planes_and_infinite_psnr_as_valid_json(self):
        log = ('[Parsed_psnr_8 @ 123] PSNR y:inf u:51.20 v:52.30 average:inf min:49.0 max:inf\n'
               '[Parsed_ssim_9 @ 456] SSIM Y:0.999 U:0.998 V:1.000 All:0.999 (30.0)\n')
        result = parse_metrics(log)
        self.assertEqual(result['psnr']['average'], 'inf')
        self.assertEqual(result['psnr']['u'], 51.2)
        self.assertEqual(result['ssim']['All'], 0.999)
        json.dumps(result, allow_nan=False)

    def test_missing_incomplete_and_nan_metrics_fail(self):
        for log in ['', 'PSNR average:42\n',
                    'PSNR average:nan min:nan max:nan\nSSIM All:nan\n',
                    'PSNR average:42 min:40 max:44\nSSIM Y:0.9\n']:
            with self.subTest(log=log), self.assertRaises(ValueError):
                parse_metrics(log)

    def test_ffmpeg_failure_preserves_original_log_and_command(self):
        def failure(command, **kwargs):
            kwargs['stderr'].write(b'original failure\r\n')
            return types.SimpleNamespace(returncode=7)

        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            with patch('compare_vsr_colour.subprocess.run', side_effect=failure), \
                    self.assertRaisesRegex(RuntimeError, 'FFmpeg exited 7'):
                run_ffmpeg(['ffmpeg', 'input'], out, 'compare')
            self.assertEqual((out / 'compare.stderr.log').read_bytes(), b'original failure\r\n')
            self.assertEqual(json.loads((out / 'compare.command.json').read_text()), ['ffmpeg', 'input'])

    def test_launch_failure_writes_error_summary_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'input.mp4'
            source.touch()
            out = root / 'comparison'
            with patch('compare_vsr_colour.subprocess.run', side_effect=FileNotFoundError('missing FFmpeg')), \
                    patch('builtins.print'):
                result = main([str(source), str(source), '--ffmpeg', 'missing', '--out', str(out)])
            self.assertEqual(result, 1)
            summary = json.loads((out / 'colour-comparison.json').read_text())
            self.assertEqual(summary['error'], 'missing FFmpeg')
            self.assertNotIn('psnr', summary)


if __name__ == '__main__':
    unittest.main()
