from fractions import Fraction
from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
from vsr_server import Commands, RunCancelled, cfr_frames, cfr_origin
from vsr_everywhere import build_ffmpeg_command, validate_memory_output


class ServerChecks(unittest.TestCase):
    def test_latest_seek_cancels_the_muxer_and_stop_cancels_waits(self):
        control = Commands()
        control.accept({'cmd': 'seek', 'segment': 2, 'run': 1})
        self.assertEqual(control.next_seek(), (2, 1))
        killed = []
        control.attach(types.SimpleNamespace(kill=lambda: killed.append(True)), 1)
        control.accept({'cmd': 'seek', 'segment': 7, 'run': 2})
        control.accept({'cmd': 'seek', 'segment': 9, 'run': 3})
        self.assertTrue(killed)
        self.assertEqual(control.next_seek(), (9, 3))
        with self.assertRaises(RunCancelled):
            control.wait_for_slot(96, 48, 1)
        control.accept({'cmd': 'stop'})
        self.assertIsNone(control.next_seek())

    def test_commands_validate_types_and_bounds(self):
        for command in [{'cmd': 'seek', 'segment': -1, 'run': 1}, {'cmd': 'seek', 'segment': True, 'run': 1},
                        {'cmd': 'seek', 'segment': 0, 'run': '1'}, {'cmd': 'limit', 'segment': 1000000}, {'cmd': 'unknown'}]:
            with self.subTest(command=command), self.assertRaises(ValueError):
                Commands().accept(command)

    def test_cfr_normalizes_the_origin_and_handles_gaps_and_duplicates(self):
        frames = [types.SimpleNamespace(pts=p, label=v) for p, v in [(100, 'a'), (101, 'b'), (101, 'duplicate'), (103, 'd')]]
        container = types.SimpleNamespace(decode=lambda stream: iter(frames))
        stream = types.SimpleNamespace(time_base=Fraction(1, 24), start_time=100)
        self.assertEqual([(frame.label, slot) for frame, slot in cfr_frames(container, stream, Fraction(24), 1, 5)],
                         [('b', 1), ('b', 2), ('d', 3), ('d', 4)])

    def test_constant_rate_keeps_frames_with_half_frame_timestamp_jitter(self):
        fps = Fraction(24000, 1001)
        points = [0, 63, 104, 146, 167, 230, 271, 313, 355, 396, 438, 480,
                  522, 563, 605, 647, 688, 730, 772, 813, 855, 897, 939, 980]
        frames = [types.SimpleNamespace(pts=pts, label=index) for index, pts in enumerate(points)]
        stream = types.SimpleNamespace(time_base=Fraction(1, 1000), start_time=0, guessed_rate=fps)
        container = types.SimpleNamespace(decode=lambda stream: iter(frames))
        self.assertEqual([(frame.label, slot) for frame, slot in cfr_frames(container, stream, fps, 0, len(frames))],
                         [(index, index) for index in range(len(frames))])

    def test_constant_rate_keeps_packed_b_frames_in_decoded_order(self):
        fps = Fraction(24000, 1001)
        points = [1, 2, 4, 3, 5, 7, 6, 8, 10, 9, 11, 13, 12, 14, 16, 15]
        frames = [types.SimpleNamespace(pts=pts, label=index) for index, pts in enumerate(points)]
        stream = types.SimpleNamespace(time_base=Fraction(125000, 2997003), start_time=0, guessed_rate=fps)
        container = types.SimpleNamespace(decode=lambda stream: iter(frames))
        self.assertEqual([(frame.label, slot) for frame, slot in cfr_frames(container, stream, fps, 0, len(frames))],
                         [(index, index) for index in range(len(frames))])

    def test_constant_rate_seek_uses_calibrated_origin_and_keeps_preroll_order(self):
        fps = Fraction(24)
        points = [1, 2, 4, 3, 5, 7, 6, 8, 10, 9, 11, 13, 12, 14, 16, 15]
        stream = types.SimpleNamespace(time_base=1 / fps, start_time=0, guessed_rate=fps)
        first = [types.SimpleNamespace(pts=pts) for pts in points]
        origin = cfr_origin(types.SimpleNamespace(decode=lambda stream: iter(first)), stream, fps)
        self.assertEqual(origin, 1)
        frames = [types.SimpleNamespace(pts=pts + 480, label=index) for index, pts in enumerate(points)]
        container = types.SimpleNamespace(decode=lambda stream: iter(frames))
        self.assertEqual([(frame.label, slot) for frame, slot in cfr_frames(container, stream, fps, 484, 490, origin)],
                         [(index, 480 + index) for index in range(4, 10)])

    def test_variable_rate_still_uses_timestamps_for_real_gaps(self):
        fps = Fraction(24)
        frames = [types.SimpleNamespace(pts=pts, label=index) for index, pts in enumerate([0, 1, 4])]
        stream = types.SimpleNamespace(time_base=1 / fps, start_time=0, guessed_rate=30)
        container = types.SimpleNamespace(decode=lambda stream: iter(frames))
        self.assertIsNone(cfr_origin(container, stream, fps))
        self.assertEqual([(frame.label, slot) for frame, slot in cfr_frames(container, stream, fps, 0, 5)],
                         [(0, 0), (1, 1), (1, 2), (1, 3), (2, 4)])

    def test_seek_muxer_uses_absolute_time_and_numbered_urls(self):
        destination = 'http://127.0.0.1:8096/VsrEverywhere/ingest/' + 'a' * 48 + '/7/index.m3u8'
        validate_memory_output(destination, 'b' * 64)
        command = build_ffmpeg_command('ffmpeg', 'source.mp4', destination, 3840, 2160, Fraction(24000, 1001),
                                       20, 'fmp4', start=120.12, encoded_video=True, memory_output=True,
                                       ingest_key='b' * 64, start_segment=60)
        self.assertEqual(command[command.index('-start_number') + 1], '60')
        self.assertEqual(command[command.index('-itsoffset') + 1], '120.120000000')
        self.assertIn('-copyts', command)
        self.assertEqual(command[command.index('-hls_segment_options') + 1], 'movflags=+frag_discont+skip_sidx')
        self.assertIn('video_full_range_flag=0', command[command.index('-bsf:v') + 1])


if __name__ == '__main__':
    unittest.main()
