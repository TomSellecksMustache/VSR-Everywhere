"""Bounded decode, CUDA, and pipe stages for the NV12 fallback path."""
from __future__ import annotations

from queue import Empty, Full, Queue
import threading
import time

from vsr_colour import rgb_to_yuv420


class _Stopped(Exception):
    pass


def write_all(pipe, data):
    view = memoryview(data).cast('B')
    total = 0
    while total < len(view):
        written = pipe.write(view[total:])
        if written is None or written <= 0:
            raise BrokenPipeError('Encoder stopped accepting frame data')
        total += written
    return total


def run_threaded(container, video_stream, create_effect, torch, np, geometry, args, process, stats, progress):
    width, height = video_stream.codec_context.width, video_stream.codec_context.height
    inputs = [torch.empty((height, width, 3), dtype=torch.uint8, pin_memory=True) for _ in range(3)]
    outputs = [torch.empty((geometry.vsr_height * 3 // 2, geometry.vsr_width), dtype=torch.uint8,
                           pin_memory=True) for _ in range(3)]
    free, decoded, encoded = Queue(3), Queue(2), Queue(2)
    for slot in range(3):
        free.put(slot)
    stop = threading.Event()
    errors = []
    count = [0, 0]
    # shortcut: nvvfx RGB8 conversion uses stream 0; use it until the wrapper forwards stream_ptr.
    cuda_stream = torch.cuda.default_stream()
    cuda_stream.wait_stream(torch.cuda.current_stream())

    def take(queue):
        while not stop.is_set():
            try:
                return queue.get(timeout=0.05)
            except Empty:
                pass
        raise _Stopped()

    def put(queue, value):
        while not stop.is_set():
            try:
                queue.put(value, timeout=0.05)
                return
            except Full:
                pass
        raise _Stopped()

    def decode():
        frames = iter(container.decode(video_stream))
        limit = int(args.seconds * float(video_stream.average_rate or 24)) if args.seconds else 0
        produced = 0
        while not limit or produced < limit:
            slot = take(free)
            start = time.perf_counter()
            try:
                frame = next(frames)
            except StopIteration:
                break
            if args.start_time and frame.time is not None and frame.time < args.start_time:
                put(free, slot)
                continue
            np.copyto(inputs[slot].numpy(), frame.to_ndarray(format='rgb24'))
            stats.add('decode', (time.perf_counter() - start) * 1000)
            put(decoded, slot)
            produced += 1
        put(decoded, None)

    def enhance():
        with torch.inference_mode(), torch.cuda.stream(cuda_stream):
            # VFX initialization, inference, and cleanup must share one CUDA thread.
            effect = create_effect(cuda_stream.cuda_stream)
            try:
                while True:
                    slot = take(decoded)
                    if slot is None:
                        put(encoded, None)
                        return
                    with stats.gpu('upload', torch):
                        tensor = inputs[slot].to('cuda:0', non_blocking=True).permute(2, 0, 1).float().div(255).contiguous()
                    with stats.gpu('vsr', torch):
                        result = effect.run(tensor, stream_ptr=cuda_stream.cuda_stream, non_blocking=False)
                        enhanced = torch.from_dlpack(result.image).clone()
                    with stats.gpu('convert_download', torch):
                        # Match the legacy RGB24 quantization and observed swscale BT.709 matrix.
                        rgb = enhanced.clamp(0, 1).mul(255).round().div(255)
                        surface = rgb_to_yuv420(rgb, bits=8, matrix='bt709')
                        outputs[slot].copy_(surface, non_blocking=True)
                    ready = torch.cuda.Event()
                    ready.record()
                    put(encoded, (slot, ready, stats.take_events()))
            finally:
                try:
                    cuda_stream.synchronize()
                finally:
                    effect.close()

    def write():
        while True:
            packet = take(encoded)
            if packet is None:
                return
            slot, ready, events = packet
            ready.synchronize()
            stats.resolve(events)
            with stats.cpu('write'):
                size = write_all(process.stdin, outputs[slot].numpy())
            count[0] += 1
            count[1] += size
            progress(*count)
            # This slot stays owned by the writer through both D2H and the complete pipe write.
            put(free, slot)

    def guard(stage):
        try:
            stage()
        except _Stopped:
            pass
        except BaseException as exc:
            errors.append(exc)
            stop.set()

    threads = [threading.Thread(target=guard, args=(stage,), name='vsr-' + stage.__name__, daemon=True)
               for stage in (decode, enhance, write)]
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            if errors and process.poll() is None:
                process.kill()
            for thread in threads:
                thread.join(timeout=0.05)
        if errors:
            raise errors[0]
        return tuple(count)
    finally:
        stop.set()
        if any(thread.is_alive() for thread in threads) and process.poll() is None:
            process.kill()
        for thread in threads:
            thread.join()
