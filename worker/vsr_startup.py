"""Keep one encoder and source clock while enhancement models warm in the background."""
from concurrent.futures import ThreadPoolExecutor
import json


class Startup:
    def __init__(self, args, fps, load):
        self.args, self.load = args, load
        self.gop = round(2 * fps)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='vsr-warmup')
        self.future = self.effect = self.enhancements = None
        self.preview_frames = 0

    @property
    def stride(self):
        return self.enhancements.stride if self.enhancements else 1

    @property
    def generated(self):
        return self.enhancements.generated if self.enhancements else 0

    def reset(self):
        self.preview_frames = 0
        if self.enhancements:
            self.enhancements.reset()

    def frame(self, pair, fps, effect, sar, stream_ptr, stats):
        if self.future is None and self.preview_frames >= self.gop * 3:
            # Build a reserve before native model loading can stall its first CUDA call.
            self.future = self.pool.submit(self.load)
        if (self.enhancements is None and self.future is not None and pair[3] % self.gop == 0
                and (self.future.done() or self.preview_frames >= self.gop * 4)):
            self.effect, self.enhancements = self.future.result()
            self.enhancements.reset()
            print(json.dumps({'event': 'enhancements_ready', 'preview_frames': self.preview_frames,
                              'preview_seconds': float(self.preview_frames / fps),
                              'media_time': float(pair[3] / fps)}), flush=True)
        if self.enhancements:
            return self.enhancements.frame(pair, fps, self.effect, sar, stream_ptr, stats)
        self.preview_frames += 1
        return self.preview(pair[0], sar, stats)

    def preview(self, frame, sar, stats):
        import numpy as np
        import torch
        from vsr_gpu import fit_rgb
        from vsr_colour import sdr_to_hdr
        with stats.gpu('preview', torch):
            rgb = np.ascontiguousarray(frame.to_ndarray(format='rgb24'))
            tensor = torch.from_numpy(rgb).to('cuda:0').permute(2, 0, 1).float().div_(255).contiguous()
            result = fit_rgb(tensor, self.args.stream_width, self.args.stream_height, sar).clamp_(0, 1)
            if self.args.auto_hdr:
                result = sdr_to_hdr(result)
        return result

    def observe(self, elapsed_ms):
        if self.enhancements:
            self.enhancements.observe(elapsed_ms)

    def close(self):
        self.pool.shutdown(wait=True)
        future, self.future = self.future, None
        if future is not None and future.exception() is None:
            effect, enhancements = future.result()
            enhancements.close()
            effect.close()
