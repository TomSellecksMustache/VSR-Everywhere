"""Optional resident Veyra NR, NVIDIA HDR, and fractional frame interpolation."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import json
import os
import struct
import subprocess
import tempfile
import time
import math

from vsr_threaded import write_all


def read_exact(pipe, size):
    result = bytearray()
    while len(result) < size:
        part = pipe.read(size - len(result))
        if not part:
            raise RuntimeError('Veyra filter closed its output pipe')
        result.extend(part)
    return result


class VeyraFilter:
    def __init__(self, width, height, profile, executable):
        if os.name != 'nt':
            raise RuntimeError('Veyra neural rendering requires Windows and DirectX 12')
        self.width, self.height = width, height
        self.reset = True
        self.log = tempfile.TemporaryFile()
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix='veyra-read')
        self.proc = None
        try:
            executable = Path(executable).resolve(strict=True)
            self.proc = subprocess.Popen([str(executable), str(width), str(height), profile],
                                         cwd=executable.parent, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=self.log, bufsize=0, creationflags=0x08000000)
            ready = self._read(lambda: json.loads(self.proc.stdout.readline(4096)), 120)
            if ready != {'event': 'ready', 'format': 'rgba8-srgb'}:
                raise RuntimeError('Unexpected Veyra filter handshake')
        except BaseException:
            self.close()
            raise

    def _read(self, action, timeout=60):
        try:
            return self.reader.submit(action).result(timeout=timeout)
        except (TimeoutError, RuntimeError, ValueError) as exc:
            self.log.seek(0)
            detail = self.log.read()[-2000:].decode(errors='replace')
            raise RuntimeError('Veyra filter failed or timed out: ' + detail) from exc

    def run(self, frame, slot, fps):
        import numpy as np
        # The native ingress contract is limited-range BT.709 NV12.
        nv12 = frame.reformat(format='nv12', dst_colorspace='ITU709').to_ndarray()
        payload = np.ascontiguousarray(nv12).tobytes()
        if len(payload) != self.width * self.height * 3 // 2:
            raise RuntimeError('Unexpected Veyra NV12 frame size')
        header = struct.pack('<IIIId', 0x56535246, len(payload), int(self.reset), slot, float(slot / fps * 1000))
        write_all(self.proc.stdin, header)
        write_all(self.proc.stdin, payload)
        self.reset = False

        def receive():
            magic, size = struct.unpack('<II', read_exact(self.proc.stdout, 8))
            if magic != 0x56535252 or size != self.width * self.height * 4:
                raise RuntimeError('Invalid Veyra output frame header')
            return read_exact(self.proc.stdout, size)

        data = self._read(receive)
        return np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 4)[..., :3].copy()

    def close(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=10)
            for pipe in (self.proc.stdin, self.proc.stdout):
                if pipe is not None:
                    pipe.close()
            self.proc = None
        self.reader.shutdown(wait=True, cancel_futures=True)
        self.log.close()


def unpack_hdr(packed):
    """NVIDIA TrueHDR's RGB10A2 output contains PQ-encoded BT.2020 RGB."""
    import torch
    bits = packed.to(torch.int64)
    return torch.stack(tuple(((bits >> shift) & 1023).float() / 1023 for shift in (0, 10, 20)))


class Enhancements:
    def __init__(self, args, width, height, source_fps):
        self.nr = self.hdr = self.interpolator = None
        self.args = args
        self.cache = {}
        self.generated = 0
        self.stride = 2 if args.frame_interpolation and args.neural_rendering else 1
        self.fps = float(source_fps)
        self.anchor_ms = self.base_ms = 0
        self.outputs = 0
        self.render_ms = 0
        try:
            if args.neural_rendering:
                self.nr = VeyraFilter(width, height, args.content_profile, args.veyra_filter)
            if args.auto_hdr:
                from nvvfx import TrueHDR
                self.hdr = TrueHDR(luminance=1000)
                self.hdr.load()
            if args.frame_interpolation:
                from nvvfx import VideoFrameGeneration
                encoding = (VideoFrameGeneration.ImageEncoding.RGB10A2 if args.auto_hdr
                            else VideoFrameGeneration.ImageEncoding.RGB8)
                self.interpolator = VideoFrameGeneration(image_encoding=encoding,
                    automatic_shot_change_detection_enabled=True)
                self.interpolator.input_width, self.interpolator.input_height = args.stream_width, args.stream_height
                self.interpolator.load()
        except BaseException:
            self.close()
            raise

    def reset(self):
        self.cache.clear()
        self.generated = 0
        if self.nr:
            self.nr.reset = True

    def render(self, frame, slot, fps, effect, sar, stream_ptr, stats):
        import numpy as np
        import torch
        from vsr_gpu import fit_rgb
        if slot in self.cache:
            return self.cache[slot]
        started = time.perf_counter()
        if self.nr:
            with stats.cpu('neural_rendering'):
                rgb = self.nr.run(frame, slot, fps)
        else:
            rgb = np.ascontiguousarray(frame.to_ndarray(format='rgb24'))
        with stats.gpu('upload', torch):
            tensor = torch.from_numpy(rgb).to('cuda:0').permute(2, 0, 1).float().div_(255).contiguous()
        if self.nr:
            from vsr_colour import srgb_to_bt709
            with stats.gpu('nr_colour', torch):
                tensor = srgb_to_bt709(tensor)
        with stats.gpu('vsr', torch):
            enhanced = torch.from_dlpack(effect.run(tensor, stream_ptr=stream_ptr).image).clone()
            enhanced = fit_rgb(enhanced, self.args.stream_width, self.args.stream_height, sar).clamp_(0, 1).contiguous()
        if self.hdr:
            with stats.gpu('hdr', torch):
                enhanced = torch.from_dlpack(self.hdr.run(enhanced, stream_ptr=stream_ptr).image).clone()
        self.cache[slot] = enhanced
        elapsed = (time.perf_counter() - started) * 1000
        self.render_ms += elapsed
        self.anchor_ms = self.anchor_ms * .75 + elapsed * .25 if self.anchor_ms else elapsed
        return enhanced

    def frame(self, pair, fps, effect, sar, stream_ptr, stats):
        import torch
        left, right, alpha, _, a, b = pair
        self.render_ms = 0
        # Release old GPU frames before creating the next source pair.
        for key in list(self.cache):
            if key not in (a, b):
                del self.cache[key]
        previous = self.render(left, a, fps, effect, sar, stream_ptr, stats)
        result = previous
        if alpha:
            if self.interpolator is None:
                raise RuntimeError('Requested interpolation was not initialized')
            current = self.render(right, b, fps, effect, sar, stream_ptr, stats)
            with stats.gpu('frame_generation', torch):
                result = torch.from_dlpack(self.interpolator.run_at_timestep(
                    previous, current, float(alpha), stream_ptr=stream_ptr).image).clone()
            self.generated += 1
        return unpack_hdr(result) if self.hdr else result

    def observe(self, elapsed_ms):
        if not self.args.frame_interpolation:
            return
        base = max(0, elapsed_ms - self.render_ms)
        self.base_ms = self.base_ms * .8 + base * .2 if self.base_ms else base
        self.outputs += 1
        if self.outputs >= 8 and self.outputs % 4 == 0:
            available = 1000 / self.fps * .9 - self.base_ms - 2
            desired = min(4, max(1, math.ceil(self.anchor_ms / max(available, 1))))
            if desired > self.stride:
                self.stride += 1
            elif desired < self.stride and self.base_ms + self.anchor_ms / max(1, self.stride - 1) < 1000 / self.fps * .8:
                self.stride -= 1

    def close(self):
        self.cache.clear()
        for name in ('interpolator', 'hdr', 'nr'):
            resource = getattr(self, name)
            if resource is not None:
                resource.close()
                setattr(self, name, None)
