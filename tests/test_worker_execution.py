"""Run the actual worker control flow with fake GPU/encoder objects, without a GPU."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from fractions import Fraction
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
import vsr_everywhere as worker


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'NumPy required')
class WorkerExecutionChecks(unittest.TestCase):
    def test_frames_are_written_once_and_gpu_initialisation_failure_falls_back(self):
        import numpy as np
        lifecycle = []

        class Tensor:
            def __getattr__(self, name):
                return lambda *a, **kw: self
            def numpy(self):
                return np.zeros((2, 2, 3), dtype=np.uint8)

        class Event:
            def __init__(self, **kw): pass
            def record(self): pass
            def synchronize(self): pass
            def elapsed_time(self, end): return 0

        class Effect:
            QualityLevel = {'STREAMING_MEDIUM': 1}
            ImageEncoding = types.SimpleNamespace(RGB8=1)
            def __init__(self, **kw):
                self._effect = types.SimpleNamespace(set_cuda_stream=lambda ptr: lifecycle.append(('bind', ptr)))
            def load(self): lifecycle.append(('load', None))
            def run(self, *a, **kw): return types.SimpleNamespace(image=Tensor())
            def close(self): pass

        class Capture(io.BytesIO):
            def close(self): self.was_closed = True

        class Process:
            def __init__(self, *a, **kw): self.stdin = Capture()
            def wait(self, **kw): return 0
            def poll(self): return 0

        def fail_encoder(*a, **kw):
            raise ImportError('codec deliberately hidden')

        def fake_threaded(container, stream, create_effect, torch, np, geometry, args, process, stats, progress):
            effect = create_effect(1234)
            effect.close()
            process.stdin.write(bytes(24))
            return 2, 24

        cuda = types.SimpleNamespace(is_available=lambda: True, Event=Event,
            current_stream=lambda: types.SimpleNamespace(cuda_stream=0), reset_peak_memory_stats=lambda: None,
            max_memory_allocated=lambda: 0, memory_allocated=lambda: 0)
        torch = types.SimpleNamespace(cuda=cuda, from_numpy=lambda a: Tensor(), from_dlpack=lambda a: Tensor())
        stream = types.SimpleNamespace(codec_context=types.SimpleNamespace(width=1920, height=1080, pix_fmt='yuv420p', color_trc=1),
                                       sample_aspect_ratio=None, average_rate=24, time_base=Fraction(1, 12288))
        frame = types.SimpleNamespace(time=3.5, to_ndarray=lambda **kw: np.zeros((2, 2, 3), dtype=np.uint8))
        seeks = []
        container = types.SimpleNamespace(streams=types.SimpleNamespace(video=[stream], audio=[]),
                                          decode=lambda s: iter([frame, frame]), close=lambda: None,
                                          seek=lambda offset, **kwargs: seeks.append((offset, kwargs)))
        modules = {'av': types.SimpleNamespace(open=lambda p: container), 'torch': torch,
                   'nvvfx': types.SimpleNamespace(VideoSuperRes=Effect),
                   'vsr_gpu': types.SimpleNamespace(GpuEncoder=fail_encoder),
                   'vsr_threaded': types.SimpleNamespace(run_threaded=fake_threaded)}
        cases = [('legacy', 'serial', False), ('gpu', 'serial', False), ('legacy', 'threaded', False),
                 ('legacy', 'serial', True), ('gpu', 'serial', True), ('legacy', 'threaded', True)]
        for pipeline, schedule, memory in cases:
            with self.subTest(pipeline=pipeline, schedule=schedule, memory=memory), tempfile.TemporaryDirectory() as temp:
                lifecycle.clear()
                root = Path(temp)
                source = root / 'source.mp4'; source.touch()
                output = root / 'cache/index.m3u8'
                destination = ('http://127.0.0.1:8096/VsrEverywhere/ingest/' + 'a' * 48 + '/index.m3u8'
                               if memory else str(output))
                argv = ['worker', str(source), '--backend', 'nvidia', '--output', destination, '--hls', '--pipeline', pipeline,
                        '--start-time', '3.5', '--legacy-schedule', schedule]
                if memory:
                    argv += ['--memory-output']
                capture = Process()
                text = io.StringIO()
                with patch.object(sys, 'argv', argv), patch.dict(sys.modules, modules), \
                     patch.dict(worker.os.environ, {'VSR_INGEST_KEY': 'b' * 64}), \
                     patch.object(worker.subprocess, 'Popen', return_value=capture), \
                     patch.object(worker, 'process_resources', return_value={}), contextlib.redirect_stdout(text):
                    worker.run(worker.parse_args())
                events = [json.loads(line) for line in text.getvalue().splitlines()]
                report = events[-1]
                if memory:
                    self.assertEqual(list(root.iterdir()), [source])
                else:
                    self.assertEqual(report, json.loads((output.parent / 'vsr-result.json').read_text()))
                self.assertEqual(report['frames'], 2)
                self.assertEqual(report['pipe_bytes'], 24)
                self.assertEqual(len(capture.stdin.getvalue()), 24)
                self.assertTrue(capture.stdin.was_closed)
                self.assertEqual(any(e.get('event') == 'fallback' for e in events), pipeline == 'gpu')
                self.assertEqual(report['pipeline'], 'legacy')
                self.assertEqual(seeks[-1], (43008, {'stream': stream, 'any_frame': False, 'backward': True}))
                self.assertEqual(lifecycle, ([('bind', 1234), ('load', None)] if schedule == 'threaded'
                                             else [('load', None)]))


if __name__ == '__main__':
    unittest.main()
