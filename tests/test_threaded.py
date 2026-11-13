import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
from vsr_metrics import StageMetrics
from vsr_threaded import run_threaded, write_all


class PipeChecks(unittest.TestCase):
    def test_partial_writes_send_every_byte_once(self):
        class Partial(io.BytesIO):
            def write(self, data):
                return super().write(data[:3])
        pipe = Partial()
        self.assertEqual(write_all(pipe, bytes(range(19))), 19)
        self.assertEqual(pipe.getvalue(), bytes(range(19)))

    def test_zero_length_write_is_an_error(self):
        with self.assertRaises(BrokenPipeError):
            write_all(types.SimpleNamespace(write=lambda data: 0), b'x')


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('numpy'), 'CPU PyTorch and NumPy required')
class ThreadedChecks(unittest.TestCase):
    def execute(self, failure=None):
        import numpy as np
        import torch
        original_empty, original_to = torch.empty, torch.Tensor.to
        released = threading.Event()
        pipe = io.BytesIO()

        class Event:
            def __init__(self, **kwargs): pass
            def record(self): pass
            def synchronize(self): pass
            def elapsed_time(self, other): return 0

        class Stream:
            cuda_stream = 1234
            def wait_stream(self, other): pass
            def synchronize(self): pass

        class Effect:
            def __init__(self):
                self.owner = threading.get_ident()
                self.closed = False
                self.device = 0
                self._effect = self
                self_thread.assertEqual(threading.current_thread().name, 'vsr-enhance')
            def load(self):
                self_thread.assertEqual(threading.get_ident(), self.owner)
            def run(self, frame, **kwargs):
                self_thread.assertEqual(threading.get_ident(), self.owner)
                self_thread.assertEqual(kwargs['stream_ptr'], 1234)
                self_thread.assertFalse(kwargs['non_blocking'])
                if failure == 'gpu':
                    raise RuntimeError('gpu failure')
                return types.SimpleNamespace(image=frame)
            def close(self):
                self_thread.assertEqual(threading.get_ident(), self.owner)
                self.closed = True

        self_thread = self
        effects = []
        def create_effect(stream_ptr):
            self.assertEqual(stream_ptr, 1234)
            effect = Effect()
            effects.append(effect)
            effect.load()
            return effect
        self.addCleanup(lambda: self.assertTrue(effects and all(effect.closed for effect in effects)))

        class Sink:
            def write(self, data):
                if failure == 'writer':
                    raise BrokenPipeError('writer failure')
                if failure == 'blocked':
                    if not released.wait(5):
                        raise TimeoutError('writer was not unblocked')
                    raise BrokenPipeError('writer unblocked')
                return pipe.write(data[:5])

        process = types.SimpleNamespace(stdin=Sink(), poll=lambda: 0 if released.is_set() else None,
                                        kill=released.set)
        stream = types.SimpleNamespace(codec_context=types.SimpleNamespace(width=4, height=4), average_rate=24)
        geometry = types.SimpleNamespace(vsr_width=4, vsr_height=4)
        args = types.SimpleNamespace(seconds=0, start_time=0)

        def frames():
            for level in range(8):
                if level == 2 and failure in ('decode', 'blocked'):
                    raise RuntimeError('decode failure')
                rgb = np.full((4, 4, 3), level * 25, dtype=np.uint8)
                yield types.SimpleNamespace(time=level / 24, to_ndarray=lambda rgb=rgb, **kwargs: rgb)

        def empty(*args, **kwargs):
            kwargs.pop('pin_memory', None)
            return original_empty(*args, **kwargs)

        def to(tensor, *args, **kwargs):
            if args and args[0] == 'cuda:0':
                return tensor
            return original_to(tensor, *args, **kwargs)

        container = types.SimpleNamespace(decode=lambda video: frames())
        progress = []
        with patch.object(torch, 'empty', side_effect=empty), patch.object(torch.Tensor, 'to', to), \
             patch.object(torch.cuda, 'default_stream', return_value=Stream()), patch.object(torch.cuda, 'Event', Event), \
             patch.object(torch.cuda, 'current_stream', return_value=Stream()), \
             patch.object(torch.cuda, 'stream', side_effect=lambda stream: contextlib.nullcontext()):
            result = run_threaded(container, stream, create_effect, torch, np, geometry, args, process,
                                  StageMetrics(), lambda count, size: progress.append((count, size)))
        return result, pipe.getvalue(), progress

    def test_order_and_buffer_reuse_with_partial_pipe_writes(self):
        result, data, progress = self.execute()
        self.assertEqual(result, (8, 8 * 24))
        self.assertEqual(progress, [(n, n * 24) for n in range(1, 9)])
        for frame in range(8):
            payload = data[frame * 24:(frame + 1) * 24]
            expected_y = round(16 + 219 * frame * 25 / 255)
            self.assertEqual(payload[:16], bytes([expected_y]) * 16)
            self.assertEqual(payload[16:], bytes([128]) * 8)

    def test_stage_failures_propagate_and_release_other_threads(self):
        for failure in ('decode', 'gpu', 'writer'):
            with self.subTest(failure=failure), self.assertRaisesRegex((RuntimeError, BrokenPipeError), failure + ' failure'):
                self.execute(failure)

    def test_decode_error_unblocks_a_pipe_writer(self):
        with self.assertRaisesRegex(RuntimeError, 'decode failure'):
            self.execute('blocked')


if __name__ == '__main__':
    unittest.main()
