from fractions import Fraction
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
from vsr_temporal import source_pairs


class TemporalChecks(unittest.TestCase):
    def test_invalid_sampling_ranges(self):
        for stride in (0, 5, '2'):
            with self.assertRaises(ValueError):
                list(source_pairs([(object(), 0)], 0, 1, lambda: stride))
        with self.assertRaises(ValueError):
            list(source_pairs([], -1, 10, lambda: 1))
        self.assertEqual(list(source_pairs([], 0, 1, lambda: 1)), [])

    def test_two_source_frames_per_anchor_preserves_output_count(self):
        frames = [(object(), slot) for slot in range(24)]
        samples = list(source_pairs(frames, 0, 24, lambda: 2))
        self.assertEqual(len(samples), 24)
        self.assertEqual(samples[1][2:], (Fraction(1, 2), 1, 0, 2))
        self.assertIs(samples[1][0], frames[0][0])
        self.assertIs(samples[1][1], frames[2][0])

    def test_adaptive_sampling_and_seek_never_change_source_clock(self):
        steps = iter([2, 3, 1, 4, 2, 4])
        first, end = 86448, 86489
        samples = list(source_pairs(((object(), slot) for slot in range(first, end)), first, end, lambda: next(steps, 4)))
        self.assertEqual([sample[3] for sample in samples], list(range(first, end)))
        for rate in (Fraction(24000, 1001), Fraction(30000, 1001), Fraction(25), Fraction(60)):
            for _, _, alpha, slot, a, b in samples:
                self.assertEqual((a + alpha * (b-a)) / rate, Fraction(slot) / rate)

    def test_disabled_interpolation_keeps_every_original_frame(self):
        frames = [(object(), slot) for slot in range(24)]
        untouched = list(source_pairs(frames, 0, 24, lambda: 1))
        self.assertEqual([sample[0] for sample in untouched], [frame for frame, _ in frames])
        smooth = list(source_pairs(frames, 0, 24, lambda: 4))
        self.assertEqual(len(smooth), 24)
        self.assertGreater(sum(sample[2] > 0 for sample in smooth), 12)
        self.assertIs(smooth[-1][0], frames[-1][0])
        self.assertEqual(smooth[-1][2], 0)

    def test_stride_adapts_to_render_budget_without_changing_rate(self):
        from types import SimpleNamespace
        from vsr_enhancements import Enhancements
        state = Enhancements.__new__(Enhancements)
        state.args = SimpleNamespace(frame_interpolation=True)
        state.fps, state.anchor_ms, state.base_ms = 24, 60, 0
        state.outputs, state.stride, state.render_ms = 0, 1, 0
        for _ in range(24): state.observe(15)
        self.assertEqual(state.stride, 3)
        state.anchor_ms = 4
        for _ in range(40): state.observe(2)
        self.assertEqual(state.stride, 1)
        self.assertEqual(state.fps, 24)


if __name__ == '__main__':
    unittest.main()
