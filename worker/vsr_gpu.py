"""GPU NV12/P010 conversion and HEVC encoding for the pinned PyNvVideoCodec wheel."""
from __future__ import annotations

from fractions import Fraction
import importlib

from vsr_capabilities import probe_codec
from vsr_colour import rgb_to_yuv420


def fit_rgb(rgb, width, height, sar):
    import torch.nn.functional as F
    source_height, source_width = rgb.shape[1:]
    if (source_width, source_height) == (width, height) and sar == 1:
        return rgb
    displayed_width = source_width * float(sar)
    scale = min(width / displayed_width, height / source_height)
    active_width = min(width, max(2, round(displayed_width * scale / 2) * 2))
    active_height = min(height, max(2, round(source_height * scale / 2) * 2))
    resized = F.interpolate(rgb.unsqueeze(0), size=(active_height, active_width),
                            mode='bicubic', align_corners=False)[0]
    left, top = (width - active_width) // 2, (height - active_height) // 2
    return F.pad(resized, (left, width - active_width - left, top, height - active_height - top))


class _CudaInput:
    """Adapt the codec's positional DLPack call to PyTorch's keyword-only API."""
    def __init__(self, tensor):
        self.tensor = tensor

    def cuda(self):
        return self

    def __dlpack__(self, stream=None):
        return self.tensor.__dlpack__(stream=stream)

    def __dlpack_device__(self):
        return self.tensor.__dlpack_device__()

    def __getattr__(self, name):
        return getattr(self.tensor, name)


class GpuEncoder:
    def __init__(self, args, geometry, fps, sar):
        self._encoder = None
        self._stream = None
        self._surface = None
        self._flushed = False
        self._closed = False
        self._frames = 0
        self._packet_index = 0
        self._mux = None
        self._sink = _PacketSink()
        self._geometry = geometry
        self._width, self._height = args.stream_width, args.stream_height
        self._sar = sar
        self._hdr = getattr(args, 'auto_hdr', False)
        if args.nvenc_lookahead:
            raise NotImplementedError('GPU encoder cannot disable adaptive keyframes with lookahead; using legacy encoding')
        if args.nvenc_preset not in {'p4', 'p5', 'p6', 'p7'} or args.nvenc_tune not in {'default', 'hq'}:
            raise ValueError('Unsupported GPU encoder preset or tune')
        if not 4 <= args.video_mbps <= 60 or not 0 <= args.nvenc_cq <= 35:
            raise ValueError('Invalid GPU encoder bitrate or CQ')
        fps = Fraction(fps)
        self._fps, self._time_base, self._gop = fps, 1 / fps, round(2 * fps)
        if not 1 <= fps <= 120 or sar <= 0 or any(size <= 0 or size % 2 for size in (self._width, self._height)):
            raise ValueError('Invalid GPU encoder geometry or frame rate')
        nvc = importlib.import_module('PyNvVideoCodec')
        if getattr(nvc, '__version__', None) != '2.2.3':
            raise RuntimeError('GPU encoding requires the verified PyNvVideoCodec 2.2.3 runtime')
        capabilities = probe_codec()
        input_format = 'P010' if self._hdr else 'NV12'
        if not capabilities.get('capabilities_verified') or input_format not in capabilities['hevc_input_formats']:
            raise RuntimeError(f'GPU HEVC {input_format} is unavailable: ' + capabilities.get('reason', '10-bit encode not supported'))
        caps = capabilities.get('caps', {})
        if any(caps.get(key) and size > caps[key] for key, size in [('width_max', self._width), ('height_max', self._height)]):
            raise RuntimeError('Requested stream resolution exceeds GPU HEVC limits')
        if args.nvenc_temporal_aq and not caps.get('support_temporal_aq'):
            raise RuntimeError('GPU does not support requested temporal AQ')
        self._torch = importlib.import_module('torch')
        self._nvc = nvc
        if not self._torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable for GPU encoding')
        self._stream = self._torch.cuda.Stream(device=0)
        options = dict(codec='hevc', gpu_id=0, cudacontext=0, cudastream=self._stream.cuda_stream,
                       preset=args.nvenc_preset.upper(), tuning_info='high_quality',
                       fps=f'{fps.numerator}/{fps.denominator}', gop=round(2 * fps),
                       idrperiod=round(2 * fps), bf=0, lookahead=0, rc='vbr',
                       bitrate=args.video_mbps * 1_000_000,
                       maxbitrate=args.video_mbps * 1_000_000,
                       vbvbufsize=args.video_mbps * 2_000_000, repeatspspps=1)
        # In 2.2.3 these options enable AQ by presence, including a supplied zero.
        if args.nvenc_spatial_aq:
            options['aq'] = 8
        if args.nvenc_temporal_aq:
            options['temporalaq'] = 1
        try:
            self._encoder = nvc.CreateEncoder(self._width, self._height, input_format, False, **options)
            if args.nvenc_cq:
                # Constructor cq clears bitrate limits; reconfiguration preserves them.
                params = self._encoder.GetEncodeReconfigureParams()
                params.targetQuality = args.nvenc_cq
                if not self._encoder.Reconfigure(params):
                    raise RuntimeError('GPU encoder rejected capped VBR/CQ configuration')
        except BaseException:
            self.close()
            raise

    def _packets(self, packets):
        if not isinstance(packets, list) or any(not isinstance(packet, dict) or not isinstance(packet.get('data'), bytes)
                                                for packet in packets):
            raise RuntimeError('Unexpected PyNvVideoCodec packet format')
        if not packets:
            return b''
        av = importlib.import_module('av')
        if self._mux is None:
            self._mux = av.open(self._sink, mode='w', format='mpegts',
                                options={'mpegts_copyts': '1', 'flush_packets': '1'})
            self._mux_stream = self._mux.add_mux_stream('hevc', rate=self._fps, width=self._width,
                                                        height=self._height, time_base=self._time_base)
        for record in packets:
            packet = av.Packet(record['data'])
            packet.stream = self._mux_stream
            packet.pts = packet.dts = self._packet_index
            packet.duration, packet.time_base = 1, self._time_base
            packet.is_keyframe = self._packet_index % self._gop == 0
            self._mux.mux(packet)
            self._packet_index += 1
        return self._sink.take()

    def encode(self, rgb):
        if self._closed or self._flushed:
            raise RuntimeError('GPU encoder is closed or already flushed')
        if (not rgb.is_cuda or rgb.device.index not in (None, 0)
                or tuple(rgb.shape) != (3, self._geometry.vsr_height, self._geometry.vsr_width)):
            raise ValueError('Expected the VSR CHW RGB tensor on CUDA device 0')
        self._stream.wait_stream(self._torch.cuda.current_stream(0))
        with self._torch.cuda.stream(self._stream):
            self._surface = _CudaInput(rgb_to_yuv420(fit_rgb(rgb, self._width, self._height, self._sar),
                bits=10 if self._hdr else 8, matrix='bt2020' if self._hdr else 'bt709'))
        # Raw DLPack input has no consumer-stream handoff; keep its allocation until copying finishes.
        self._stream.synchronize()
        try:
            picture = self._nvc.NV_ENC_PIC_PARAMS()
            picture.inputTimeStamp = self._frames
            packets = self._encoder.Encode(self._surface, picture)
            self._frames += 1
            return self._packets(packets)
        finally:
            self._stream.synchronize()
            self._surface = None

    def flush(self):
        if self._closed or self._flushed:
            return b''
        result = self._packets(self._encoder.EndEncode())
        if self._mux is not None:
            self._mux.close()
            self._mux = None
            result += self._sink.take()
        self._flushed = True
        return result

    def close(self):
        if self._closed:
            return
        try:
            if self._stream is not None:
                self._stream.synchronize()
        finally:
            if self._mux is not None:
                self._mux.close()
                self._mux = None
            self._surface = None
            self._encoder = None
            self._closed = True


class _PacketSink:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(bytes(data))
        return len(data)

    def take(self):
        data = b''.join(self.chunks)
        self.chunks.clear()
        return data
