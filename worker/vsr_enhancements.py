"""Resident NVIDIA HDR and fractional frame interpolation."""
import time


def unpack_hdr(packed):
    """NVIDIA TrueHDR's RGB10A2 output contains PQ-encoded BT.2020 RGB."""
    import torch
    bits = packed.to(torch.int64)
    return torch.stack(tuple(((bits >> shift) & 1023).float() / 1023 for shift in (0, 10, 20)))


class Enhancements:
    def __init__(self, args, source_fps):
        self.hdr = self.interpolator = None
        self.args = args
        self.cache = {}
        self.generated = 0
        self.stride = 1
        self.fps = float(source_fps)
        self.anchor_ms = self.base_ms = 0
        self.generation_ms = self.generated_ms = 0
        self.outputs = 0
        self.render_ms = 0
        try:
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

    def render(self, frame, slot, effect, sar, stream_ptr, stats):
        import torch
        from vsr_gpu import fit_rgb
        if slot in self.cache:
            return self.cache[slot]
        started = time.perf_counter()
        from vsr_colour import frame_rgb
        rgb, maximum = frame_rgb(frame)
        with stats.gpu('upload', torch):
            tensor = torch.from_numpy(rgb).to('cuda:0').permute(2, 0, 1).float().div_(maximum).contiguous()
        with stats.gpu('vsr', torch):
            enhanced = torch.from_dlpack(effect.run(tensor, stream_ptr=stream_ptr).image).clone() if effect is not None else tensor
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
        self.render_ms = self.generated_ms = 0
        # Release old GPU frames before creating the next source pair.
        for key in list(self.cache):
            if key not in (a, b):
                del self.cache[key]
        previous = self.render(left, a, effect, sar, stream_ptr, stats)
        result = previous
        if alpha:
            if self.interpolator is None:
                raise RuntimeError('Requested interpolation was not initialized')
            current = self.render(right, b, effect, sar, stream_ptr, stats)
            started = time.perf_counter()
            with stats.gpu('frame_generation', torch):
                result = torch.from_dlpack(self.interpolator.run_at_timestep(
                    previous, current, float(alpha), stream_ptr=stream_ptr).image).clone()
            self.generated_ms = (time.perf_counter() - started) * 1000
            self.generation_ms = self.generation_ms * .75 + self.generated_ms * .25 if self.generation_ms else self.generated_ms
            self.generated += 1
        return unpack_hdr(result) if self.hdr else result

    def observe(self, elapsed_ms):
        if not self.args.frame_interpolation:
            return
        base = max(0, elapsed_ms - self.render_ms - self.generated_ms)
        self.base_ms = self.base_ms * .8 + base * .2 if self.base_ms else base
        self.outputs += 1
        if self.outputs >= 8 and self.outputs % 4 == 0:
            interval = 1000 / self.fps
            # More sampling saves anchor work but adds interpolation work.
            costs = [self.base_ms + self.anchor_ms / stride + self.generation_ms * (stride - 1) / stride
                     for stride in range(1, 5)]
            fits = [stride for stride in range(1, 5) if costs[stride - 1] <= interval * .9 - 2]
            desired = fits[0] if fits else min(range(1, 5), key=lambda stride: costs[stride - 1])
            if desired > self.stride or (desired < self.stride and (not fits or costs[desired - 1] < interval * .8)):
                self.stride = desired

    def close(self):
        self.cache.clear()
        for name in ('interpolator', 'hdr'):
            resource = getattr(self, name)
            if resource is not None:
                resource.close()
                setattr(self, name, None)
