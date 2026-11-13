from concurrent.futures import Future
from fractions import Fraction
from pathlib import Path
import importlib.util
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
from vsr_startup import Startup


class StartupChecks(unittest.TestCase):
    def test_preview_keeps_playing_during_load_and_switches_on_a_gop_boundary(self):
        entered, release = threading.Event(), threading.Event()
        effect = Mock()
        enhancements = Mock(stride=2, generated=1)
        enhancements.frame.return_value = 'enhanced'

        def load():
            entered.set()
            self.assertTrue(release.wait(2))
            return effect, enhancements

        state = Startup(types.SimpleNamespace(), Fraction(24000, 1001), load)
        try:
            with patch.object(state, 'preview', return_value='preview'):
                def frame(slot):
                    return state.frame((object(), object(), 0, slot, slot, slot + 1), Fraction(24000, 1001), None, 1, 0, None)
                for slot in range(144):
                    self.assertEqual(frame(slot), 'preview')
                self.assertFalse(entered.is_set())
                self.assertEqual(frame(144), 'preview')
                self.assertTrue(entered.wait(1))
                self.assertEqual(frame(145), 'preview')
                release.set()
                state.future.result(timeout=1)
                self.assertEqual(frame(146), 'preview')
                self.assertEqual(frame(192), 'enhanced')
                self.assertEqual((state.stride, state.generated), (2, 1))
                state.reset()
                self.assertEqual(frame(480), 'enhanced')
                self.assertEqual(enhancements.reset.call_count, 2)
        finally:
            release.set()
            state.close()
            state.close()
        effect.close.assert_called_once()
        enhancements.close.assert_called_once()

    def test_model_error_does_not_silently_leave_preview_running(self):
        state = Startup(types.SimpleNamespace(), 24, Mock())
        state.future = Future()
        state.future.set_exception(RuntimeError('model failed'))
        try:
            with self.assertRaisesRegex(RuntimeError, 'model failed'):
                state.frame((None, None, 0, 192, 192, 193), 24, None, 1, 0, None)
        finally:
            state.close()

    def test_preview_waits_at_eight_seconds_even_when_models_are_still_loading(self):
        future, effect, enhancements = Mock(), Mock(), Mock()
        future.done.return_value = False
        future.result.return_value = effect, enhancements
        future.exception.return_value = None
        enhancements.frame.return_value = 'enhanced'
        state = Startup(types.SimpleNamespace(), 24, Mock())
        state.future, state.preview_frames = future, 192
        try:
            with patch.object(state, 'preview') as preview:
                self.assertEqual(state.frame((None, None, 0, 192, 192, 193), 24, None, 1, 0, None), 'enhanced')
                future.result.assert_called_once()
                preview.assert_not_called()
        finally:
            state.close()

    @unittest.skipUnless(importlib.util.find_spec('torch'), 'PyTorch required')
    def test_hdr_preview_converts_pixels_instead_of_relabeling_sdr(self):
        import torch
        from vsr_colour import sdr_to_hdr
        rgb = torch.tensor([[[0, 1, 1]], [[0, 1, 0]], [[0, 1, 0]]], dtype=torch.float64)
        pq = sdr_to_hdr(rgb)
        power = pq.pow(32 / 2523)
        nits = ((power - 3424 / 4096).clamp_min(0) / (2413 / 128 - 2392 / 128 * power)).pow(16384 / 2610) * 10000
        torch.testing.assert_close(nits[:, 0, 0], torch.zeros(3, dtype=torch.float64), atol=1e-8, rtol=0)
        torch.testing.assert_close(nits[:, 0, 1], torch.full((3,), 203., dtype=torch.float64))
        torch.testing.assert_close(nits[:, 0, 2], torch.tensor([.6274039, .0690973, .0163914], dtype=torch.float64) * 203)


if __name__ == '__main__':
    unittest.main()
