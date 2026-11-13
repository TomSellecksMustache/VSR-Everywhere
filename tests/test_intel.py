"""Intel command checks and optional real OpenVINO CPU reference tests (no GPU fallback)."""
from fractions import Fraction
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
import vsr_everywhere as worker
from vsr_backends import model_path

MODEL = Path(os.environ.get('VSR_INTEL_TEST_MODEL', model_path()))
HAS_RUNTIME = importlib.util.find_spec('openvino') is not None and MODEL.is_file()
FFMPEG = os.environ.get('VSR_TEST_FFMPEG')
FFPROBE = os.environ.get('VSR_TEST_FFPROBE')


class IntelCommandChecks(unittest.TestCase):
    def test_qsv_keeps_audio_pacing_and_hls_without_nvidia_flags(self):
        for kind in ('mpegts', 'fmp4'):
            cmd = worker.build_ffmpeg_command('ffmpeg', 'source.mp4', 'cache/index.m3u8',
                5120, 2880, Fraction(24000, 1001), 20, kind, start=2.002,
                start_segment=1, backend='intel', nvenc_lookahead=12, nvenc_cq=1,
                nvenc_spatial_aq=True, nvenc_temporal_aq=True)
            self.assertEqual(cmd[cmd.index('-c:v') + 1], 'hevc_qsv')
            self.assertEqual(cmd[cmd.index('-profile:v') + 1], 'main10')
            self.assertEqual(cmd[cmd.index('-framerate') + 1], '24000/1001')
            self.assertEqual(cmd[cmd.index('-start_number') + 1], '1')
            self.assertEqual(cmd[cmd.index('-c:a') + 1], 'aac')
            self.assertIn('-forced_idr', cmd)
            self.assertIn('-copyts', cmd)
            for flag in ('-rc-lookahead', '-spatial-aq', '-temporal-aq', '-cq', '-tune', '-no-scenecut'):
                self.assertNotIn(flag, cmd)

    def test_auto_retries_only_before_ready(self):
        with patch.object(sys, 'argv', ['worker', 'source.mp4', '--output', 'cache/index.m3u8']):
            args = worker.parse_args()
        candidates = [dict(backend=name, available=True) for name in ('nvidia', 'intel')]
        def initialize(current, retained, *rest):
            if current.backend == 'nvidia':
                raise RuntimeError('effect not initialized')
            retained['backend'] = current.backend
        with patch('vsr_backends.probe', return_value=candidates), patch.object(worker, 'run_backend', side_effect=initialize) as run:
            retained = {}
            worker.run(args, retained, initialize_only=True)
            self.assertEqual(retained['backend'], 'intel')
            self.assertEqual(run.call_count, 2)
            run.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'effect not initialized'):
                worker.run(args)
            self.assertEqual(run.call_count, 1)


@unittest.skipUnless(HAS_RUNTIME, 'Install OpenVINO and set VSR_INTEL_TEST_MODEL for CPU reference tests')
class IntelModelChecks(unittest.TestCase):
    def compile(self, strength, width=16, height=12, output_width=64, output_height=48):
        import openvino as ov
        from vsr_intel import build_model
        return ov.Core().compile_model(build_model(ov.Core(), MODEL, width, height,
            output_width, output_height, strength), 'CPU', {'INFERENCE_PRECISION_HINT': 'f32'})

    def test_rgb_range_shape_and_zero_strength(self):
        import numpy as np
        image = np.zeros((1, 12, 16, 3), np.uint8)
        image[:] = [200, 60, 20]
        for strength in (0, 0.75, 1):
            compiled = self.compile(strength)
            result = compiled([image])[0]
            self.assertEqual(result.shape, (1, 48, 64, 3))
            self.assertEqual(result.dtype, np.uint8)
            np.testing.assert_allclose(result[0, 20:28, 24:40].mean(axis=(0, 1)), [200, 60, 20], atol=2)

    def test_ai_changes_detail_from_bicubic(self):
        import numpy as np
        image = np.random.default_rng(7).integers(0, 256, (1, 12, 16, 3), dtype=np.uint8)
        baseline = self.compile(0)([image])[0].copy()
        enhanced = self.compile(1)([image])[0].copy()
        self.assertGreater(np.abs(enhanced.astype(float) - baseline).mean(), 0.1)

    @unittest.skipUnless(FFMPEG and FFPROBE, 'Set VSR_TEST_FFMPEG and VSR_TEST_FFPROBE for software mux reference')
    def test_real_worker_model_and_audio_mux_reference(self):
        real_command = worker.build_ffmpeg_command
        def software_command(*args, **kwargs):
            command = real_command(*args, **kwargs)
            command[command.index('hevc_qsv')] = 'libx265'
            for flag in ('-look_ahead_depth', '-adaptive_i', '-forced_idr'):
                if flag in command:
                    index = command.index(flag)
                    del command[index:index + 2]
            return command
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.mp4'
            output = Path(temporary) / 'hls/index.m3u8'
            subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'testsrc2=size=160x90:rate=24,drawbox=color=0xc83c14:x=40:y=20:w=80:h=50:t=fill',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                '-t', '4', '-c:v', 'mpeg4', '-g', '24', '-c:a', 'aac', '-shortest', str(source)], check=True, timeout=30)
            with patch.object(sys, 'argv', ['worker', str(source), '--output', str(output), '--hls',
                '--backend', 'intel', '--intel-model', str(MODEL), '--ffmpeg', FFMPEG, '--seconds', '0.5',
                '--processing-width', '1280', '--processing-height', '720', '--stream-width', '640', '--stream-height', '360']):
                args = worker.parse_args()
            with patch('vsr_intel.intel_device', return_value='CPU'), patch.object(worker, 'build_ffmpeg_command', side_effect=software_command):
                retained = {}
                control = Mock()
                control.cancelled.return_value = False
                try:
                    worker.run(args, retained, initialize_only=True)
                    model = retained['intel_effect']
                    for run, segment in enumerate((1, 0, 1), 1):
                        args.output = str(Path(temporary) / f'run-{run}/index.m3u8')
                        args.start_time = segment * 2
                        control.wait_for_slot.reset_mock()
                        worker.run(args, retained, control=control, run_id=run, start_segment=segment)
                        self.assertIs(retained['intel_effect'], model)
                        slots = [call.args[0] for call in control.wait_for_slot.call_args_list]
                        self.assertEqual(slots, list(range(segment * 48, segment * 48 + 12)))
                    output = Path(args.output)
                finally:
                    if retained.get('intel_effect') is not None:
                        retained['intel_effect'].close()
            report = json.loads(output.with_name('vsr-result.json').read_text())
            self.assertEqual(report['frames'], 12)
            self.assertEqual(report['backend'], 'intel')
            probe = subprocess.run([FFPROBE, '-v', 'error', '-show_streams', '-of', 'json', str(output)],
                                   capture_output=True, text=True, check=True, timeout=30)
            streams = json.loads(probe.stdout)['streams']
            video = next(s for s in streams if s['codec_type'] == 'video')
            self.assertEqual((video['width'], video['height'], video['profile']), (640, 360, 'Main 10'))
            self.assertTrue(any(s['codec_name'] == 'aac' for s in streams))
            self.assertAlmostEqual(float(video['start_time']), 2, delta=1 / 24)
            audio = next(s for s in streams if s['codec_type'] == 'audio')
            self.assertAlmostEqual(float(audio['start_time']), float(video['start_time']), delta=0.05)
            import av
            import numpy as np
            with av.open(str(source)) as media:
                expected = next(media.decode(video=0)).to_ndarray(format='rgb24')[40:50, 70:90].mean(axis=(0, 1))
            with av.open(str(output)) as media:
                actual = next(media.decode(video=0)).to_ndarray(format='rgb24')[160:200, 280:360].mean(axis=(0, 1))
            np.testing.assert_allclose(actual, expected, atol=6)


if __name__ == '__main__':
    unittest.main()
