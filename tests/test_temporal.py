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

    def workload(self, anchor, generation, fixed, *, enabled=True, frames=480):
        from types import SimpleNamespace
        from vsr_enhancements import Enhancements
        state = Enhancements.__new__(Enhancements)
        state.args = SimpleNamespace(frame_interpolation=enabled)
        state.fps, state.stride = 24, 2 if enabled else 1
        state.anchor_ms = state.base_ms = state.generation_ms = 0
        state.outputs = state.render_ms = state.generated_ms = 0
        cache, records = set(), []
        for _, _, alpha, slot, a, b in source_pairs(((object(), slot) for slot in range(frames)), 0, frames, lambda: state.stride):
            cache.intersection_update((a, b))
            state.render_ms = state.generated_ms = 0
            for sample in (a, b) if alpha else (a,):
                if sample not in cache:
                    cost = anchor(slot) if callable(anchor) else anchor
                    cache.add(sample)
                    state.render_ms += cost
                    state.anchor_ms = state.anchor_ms * .75 + cost * .25 if state.anchor_ms else cost
            if alpha:
                state.generated_ms = generation
                state.generation_ms = state.generation_ms * .75 + generation * .25 if state.generation_ms else generation
            work = fixed + state.render_ms + state.generated_ms
            state.observe(work)
            records.append((slot, state.stride, work))
        self.assertEqual([record[0] for record in records], list(range(frames)))
        self.assertEqual(state.fps, 24)
        return state, records

    def test_expensive_interpolation_does_not_make_overload_worse(self):
        for generation in (30, 60):
            with self.subTest(generation=generation):
                state, records = self.workload(20, generation, 6)
                self.assertEqual(state.stride, 1)
                self.assertEqual({record[1] for record in records[-96:]}, {1})
                self.assertEqual(sum(record[2] for record in records[-96:]) / 96, 26)

    def test_full_processing_budget_settles_without_sampling_oscillation(self):
        state, records = self.workload(60, 5, 12)
        self.assertEqual(state.stride, 3)
        self.assertEqual({record[1] for record in records[-96:]}, {3})
        self.assertLess(sum(record[2] for record in records[-96:]) / 96, 1000 / 24)

    def test_cheaper_scenes_restore_every_original_frame(self):
        state, records = self.workload(lambda slot: 60 if slot < 240 else 15, 5, 12)
        self.assertIn(3, {record[1] for record in records[:240]})
        self.assertEqual(state.stride, 1)
        self.assertEqual({record[1] for record in records[-96:]}, {1})

    def test_disabled_interpolation_does_not_skip_enhancement_work(self):
        state, records = self.workload(60, 5, 12, enabled=False)
        self.assertEqual(state.stride, 1)
        self.assertEqual({record[1] for record in records}, {1})
        self.assertTrue(all(record[2] == 72 for record in records))


if __name__ == '__main__':
    unittest.main()
