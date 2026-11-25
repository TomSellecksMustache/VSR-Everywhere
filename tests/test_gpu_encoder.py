import contextlib
from fractions import Fraction
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
from vsr_gpu import GpuEncoder, fit_rgb, _PacketSink


@unittest.skipUnless(importlib.util.find_spec('torch'), 'CPU PyTorch required')
class GpuEncoderChecks(unittest.TestCase):
    def setUp(self):
        import torch
        self.torch = torch
        self.args = types.SimpleNamespace(stream_width=8, stream_height=4, video_mbps=20,
            nvenc_preset='p6', nvenc_tune='hq', nvenc_lookahead=0, nvenc_cq=19,
            nvenc_spatial_aq=True, nvenc_temporal_aq=True, auto_hdr=True)
        self.geometry = types.SimpleNamespace(vsr_width=4, vsr_height=4)

    @contextlib.contextmanager
    def runtime(self, *, version='2.2.3', ten_bit=True, reconfigure=True):
        state = {'calls': [], 'pending': None}

        class Stream:
            cuda_stream = 1234
            def __init__(self, **kwargs): pass
            def wait_stream(self, other): state['calls'].append('wait')
            def synchronize(self):
                if state['pending'] is not None:
                    assert state['pending']() is not None, 'GPU source freed before D2D copy completed'
                    state['pending'] = None
                state['calls'].append('sync')

        class Encoder:
            def __init__(self, width, height, fmt, cpu, **options):
                state['creation'] = (width, height, fmt, cpu, options)
                state['params'] = types.SimpleNamespace(averageBitrate=options['bitrate'],
                    maxBitRate=options['maxbitrate'], vbvBufferSize=options['vbvbufsize'], targetQuality=0)
            def GetEncodeReconfigureParams(self): return state['params']
            def Reconfigure(self, params):
                state['calls'].append('reconfigure')
                return reconfigure
            def Encode(self, surface, picture):
                state['calls'].append(('encode', picture.inputTimeStamp))
                state['pending'] = weakref.ref(surface)
                state['last_surface'] = weakref.ref(surface)
                self_thread.assertEqual(surface.dtype, self_thread.torch.uint16 if self_thread.args.auto_hdr else self_thread.torch.uint8)
                self_thread.assertEqual(tuple(surface.shape), (6, 8))
                self_thread.assertTrue(surface.is_contiguous())
                self_thread.assertEqual(surface.storage_offset(), 0)
                return [{'data': b'frame'}, {'data': bytes([picture.inputTimeStamp])}]
            def EndEncode(self):
                state['calls'].append('drain')
                return [{'data': b'tail-1'}, {'data': b'tail-2'}]
            def __del__(self): state['calls'].append('release')

        self_thread = self
        class Mux:
            def __init__(self, sink): self.sink = sink
            def add_mux_stream(self, *args, **kwargs): return types.SimpleNamespace(**kwargs)
            def mux(self, packet):
                state.setdefault('packet_clock', []).append((packet.pts, packet.dts, packet.time_base, packet.is_keyframe))
                self.sink.write(packet.data)
            def close(self): pass
        av = types.SimpleNamespace(open=lambda sink, **kwargs: Mux(sink), Packet=lambda data: types.SimpleNamespace(data=data))
        nvc = types.SimpleNamespace(__version__=version, CreateEncoder=Encoder,
            NV_ENC_PIC_PARAMS=types.SimpleNamespace,
            GetEncoderCaps=lambda **kwargs: {'support_10bit_encode': ten_bit, 'support_temporal_aq': True,
                                              'width_max': 8192, 'height_max': 8192})
        with patch.dict(sys.modules, {'PyNvVideoCodec': nvc, 'av': av}), \
             patch.object(self.torch.cuda, 'is_available', return_value=True), \
             patch.object(self.torch.cuda, 'Stream', Stream), \
             patch.object(self.torch.cuda, 'current_stream', return_value=Stream()), \
             patch.object(self.torch.cuda, 'stream', side_effect=lambda stream: contextlib.nullcontext()):
            yield state

    def test_constructor_maps_rates_and_cq_without_clearing_limits(self):
        with self.runtime() as state:
            encoder = GpuEncoder(self.args, self.geometry, Fraction(24000, 1001), Fraction(1))
            width, height, fmt, cpu, options = state['creation']
            self.assertEqual((width, height, fmt, cpu), (8, 4, 'P010', False))
            self.assertEqual(options['cudastream'], 1234)
            self.assertEqual((options['preset'], options['tuning_info']), ('P6', 'high_quality'))
            self.assertEqual((options['fps'], options['gop'], options['idrperiod'], options['bf']), ('24000/1001', 48, 48, 0))
            self.assertEqual((options['aq'], options['temporalaq'], options['lookahead']), (8, 1, 0))
            self.assertNotIn('cq', options)
            self.assertEqual(vars(state['params']), dict(averageBitrate=20_000_000, maxBitRate=20_000_000,
                                                       vbvBufferSize=40_000_000, targetQuality=19))
            encoder.close()
            encoder.close()
            self.assertEqual(state['calls'].count('release'), 1)

    def test_sdr_uses_nv12_without_requiring_ten_bit_hardware(self):
        self.args.auto_hdr = False
        with self.runtime(ten_bit=False) as state:
            encoder = GpuEncoder(self.args, self.geometry, Fraction(24), Fraction(1))
            self.assertEqual(state['creation'][2], 'NV12')
            gpu_rgb = types.SimpleNamespace(is_cuda=True, device=types.SimpleNamespace(index=0), shape=(3, 4, 4))
            with patch('vsr_gpu.fit_rgb', return_value=self.torch.zeros((3, 4, 8))):
                encoder.encode(gpu_rgb)
            encoder.close()

    @unittest.skipUnless(importlib.util.find_spec('av'), 'PyAV required')
    def test_installed_mux_api_accepts_external_packets_without_an_encoder(self):
        import av
        sink = _PacketSink()
        container = av.open(sink, mode='w', format='mpegts', options={'mpegts_copyts': '1', 'flush_packets': '1'})
        try:
            stream = container.add_mux_stream('hevc', rate=Fraction(24), width=3840, height=2160, time_base=Fraction(1, 24))
            packet = av.Packet(b'encoded')
            packet.stream = stream
            packet.is_keyframe = True
            self.assertTrue(packet.is_keyframe)
            self.assertIsNone(stream.codec_context)
        finally:
            container.close()

    def test_disabled_aq_is_omitted_and_failed_reconfiguration_releases_encoder(self):
        self.args.nvenc_spatial_aq = self.args.nvenc_temporal_aq = False
        with self.runtime(reconfigure=False) as state:
            with self.assertRaisesRegex(RuntimeError, 'capped VBR/CQ'):
                GpuEncoder(self.args, self.geometry, Fraction(24), Fraction(1))
            options = state['creation'][-1]
            self.assertNotIn('aq', options)
            self.assertNotIn('temporalaq', options)
            self.assertIn('release', state['calls'])

    def test_unsupported_settings_and_capabilities_fail_before_encoding(self):
        self.args.nvenc_lookahead = 8
        with self.assertRaisesRegex(NotImplementedError, 'adaptive keyframes'):
            GpuEncoder(self.args, self.geometry, Fraction(24), Fraction(1))
        self.args.nvenc_lookahead = 0
        for settings, reason in [({'version': '2.2.4'}, 'verified'), ({'ten_bit': False}, 'P010')]:
            with self.runtime(**settings) as state, self.assertRaisesRegex(RuntimeError, reason):
                GpuEncoder(self.args, self.geometry, Fraction(24), Fraction(1))
            self.assertNotIn('creation', state)

    def test_device_buffer_lifetime_packets_timestamps_and_drain(self):
        self.args.nvenc_cq = 0
        gpu_rgb = types.SimpleNamespace(is_cuda=True, device=types.SimpleNamespace(index=0), shape=(3, 4, 4))
        cpu_rgb = self.torch.ones((3, 4, 4))
        with self.runtime() as state:
            encoder = GpuEncoder(self.args, self.geometry, Fraction(24), Fraction(1))
            with patch('vsr_gpu.fit_rgb', return_value=self.torch.ones((3, 4, 8))), \
                 patch.object(self.torch.Tensor, 'cpu', side_effect=AssertionError('Frame downloaded to CPU')):
                self.assertEqual(encoder.encode(gpu_rgb), b'frame\x00')
                self.assertEqual(encoder.encode(gpu_rgb), b'frame\x01')
            self.assertIsNone(state['last_surface'](), 'P010 allocation retained after its copy completed')
            self.assertEqual(encoder.flush(), b'tail-1tail-2')
            self.assertEqual(encoder.flush(), b'')
            self.assertEqual([entry[:2] for entry in state['packet_clock']], [(n, n) for n in range(6)])
            self.assertTrue(all(entry[2] == Fraction(1, 24) for entry in state['packet_clock']))
            self.assertTrue(state['packet_clock'][0][3])
            self.assertEqual(state['calls'].count('drain'), 1)
            self.assertIn(('encode', 0), state['calls'])
            self.assertIn(('encode', 1), state['calls'])
            with self.assertRaisesRegex(RuntimeError, 'flushed'):
                encoder.encode(gpu_rgb)
            encoder.close()
        self.assertEqual(tuple(cpu_rgb.shape), (3, 4, 4))

    def test_fit_skips_matching_canvas_and_preserves_sar_with_black_padding(self):
        from vsr_colour import rgb_to_yuv420
        rgb = self.torch.ones((3, 4, 4))
        self.assertIs(fit_rgb(rgb, 4, 4, Fraction(1)), rgb)
        # A 4x4 image with SAR 2:1 fills an 8x4 canvas; square pixels keep side bars.
        self.assertTrue(self.torch.allclose(fit_rgb(rgb, 8, 4, Fraction(2)), self.torch.ones((3, 4, 8))))
        padded = fit_rgb(rgb, 8, 4, Fraction(1))
        self.assertTrue(self.torch.all(padded[:, :, :2] == 0))
        self.assertTrue(self.torch.all(padded[:, :, 2:6] == 1))
        self.assertTrue(self.torch.all(padded[:, :, 6:] == 0))
        surface = rgb_to_yuv420(padded, bits=10, matrix='bt709').to(self.torch.int32)
        self.assertTrue(self.torch.all(surface[:4, :2] == 16 * 4 * 64))
        self.assertTrue(self.torch.all(surface[:4, 2:6] == 235 * 4 * 64))
        self.assertTrue(self.torch.all(surface[4:] == 128 * 4 * 64))


if __name__ == '__main__':
    unittest.main()
