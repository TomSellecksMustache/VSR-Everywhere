"""Persistent worker commands and deterministic frame slots for on-demand HLS."""
from __future__ import annotations

import argparse
from fractions import Fraction
from itertools import chain, islice
import json
import math
from statistics import median
import sys
import threading
import time


class RunCancelled(Exception):
    pass


class Commands:
    def __init__(self):
        self.condition = threading.Condition()
        self.stopped = False
        self.pending = None
        self.run = None
        self.limit = -1
        self.process = None

    def accept(self, command):
        operation = command.get('cmd')
        if operation not in {'seek', 'limit', 'stop'}:
            raise ValueError('Unknown worker command')
        for name in (['segment', 'run'] if operation == 'seek' else ['segment'] if operation == 'limit' else []):
            value = command.get(name)
            if type(value) is not int or not 0 <= value <= (999999 if name == 'segment' else 2147483647):
                raise ValueError('Invalid ' + name)
        process = None
        with self.condition:
            if operation == 'stop':
                self.stopped = True
                process = self.process
            elif operation == 'seek':
                self.pending = (command['segment'], command['run'])
                self.run = command['run']
                self.limit = command['segment']
                process = self.process
            else:
                self.limit = command['segment']
            self.condition.notify_all()
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass

    def cancelled(self, run):
        with self.condition:
            return self.stopped or self.run != run

    def next_seek(self):
        with self.condition:
            self.condition.wait_for(lambda: self.stopped or self.pending is not None)
            if self.stopped:
                return None
            request, self.pending = self.pending, None
            return request

    def attach(self, process, run):
        with self.condition:
            if self.stopped or self.run != run:
                process.kill()
                raise RunCancelled()
            self.process = process

    def detach(self, process):
        with self.condition:
            if self.process is process:
                self.process = None

    def wait_for_slot(self, slot, gop, run):
        paused = False
        with self.condition:
            # The next IDR makes the muxer finish the preceding segment before pausing.
            while not self.stopped and self.run == run and slot > (self.limit + 1) * gop:
                if not paused:
                    print(json.dumps({'event': 'paused', 'run': run, 'segment': slot // gop - 1}), flush=True)
                    paused = True
                self.condition.wait()
            if self.stopped or self.run != run:
                raise RunCancelled()
            if paused:
                print(json.dumps({'event': 'resumed', 'run': run, 'segment': slot // gop}), flush=True)


def _constant_rate(stream, fps):
    rate = getattr(stream, 'guessed_rate', None) or getattr(stream, 'base_rate', None)
    return rate is not None and abs(Fraction(rate) - fps) <= fps / 100000


def _frame_offsets(frames, stream, fps):
    return [Fraction(frame.pts) * (getattr(frame, 'time_base', None) or stream.time_base) * fps - index
            for index, frame in enumerate(frames) if getattr(frame, 'pts', None) is not None]


def cfr_origin(container, stream, fps):
    """Calibrate the fixed-rate decoder clock once, before any source seek."""
    if not _constant_rate(stream, fps):
        return None
    offsets = _frame_offsets(islice(container.decode(stream), 32), stream, fps)
    return median(offsets) if offsets else None


def cfr_frames(container, stream, fps, start_slot, end_slot, frame_origin=None):
    """Keep fixed-rate decoded presentation order; resample genuinely variable timing."""
    frames, offset = container.decode(stream), 0
    fixed = _constant_rate(stream, fps)
    if fixed:
        prefix = list(islice(frames, 32))
        offsets = _frame_offsets(prefix, stream, fps)
        fixed = bool(offsets)
        if fixed and start_slot:
            origin = frame_origin if frame_origin is not None else Fraction(getattr(stream, 'start_time', None) or 0) * stream.time_base * fps
            offset = max(0, round(median(offsets) - origin))
        frames = chain(prefix, frames)
    slot, previous = start_slot, None
    for source_slot, frame in enumerate(frames, offset):
        # CFR timestamps can jitter or run backwards around packed B-frames.
        if not fixed and getattr(frame, 'pts', None) is not None:
            source_slot = round(Fraction(frame.pts - (getattr(stream, 'start_time', None) or 0)) * stream.time_base * fps)
        elif not fixed and getattr(frame, 'time', None) is not None:
            source_slot = round(frame.time * fps)
        if source_slot < slot:
            if slot == start_slot:
                previous = frame
            continue
        if previous is None:
            previous = frame
        while slot < min(source_slot, end_slot):
            yield previous, slot
            slot += 1
        if slot >= end_slot:
            return
        yield frame, slot
        slot += 1
        previous = frame
    while previous is not None and slot < end_slot:
        yield previous, slot
        slot += 1


def serve(args, run_worker):
    if not args.memory_output or not args.hls:
        raise ValueError('--serve requires memory HLS output')
    control, retained = Commands(), {}

    def read_commands():
        try:
            for line in sys.stdin:
                try:
                    command = json.loads(line)
                    if not isinstance(command, dict):
                        raise ValueError('Worker command must be an object')
                    control.accept(command)
                    if command.get('cmd') == 'stop':
                        break
                except (ValueError, TypeError) as exc:
                    print('VSR COMMAND ERROR: ' + str(exc), file=sys.stderr, flush=True)
        finally:
            control.accept({'cmd': 'stop'})

    try:
        # Import native libraries before starting a blocking Windows pipe reader.
        run_worker(args, retained=retained, initialize_only=True)
        threading.Thread(target=read_commands, name='vsr-commands', daemon=True).start()
        ready = retained['ready']
        fps = Fraction(ready['fps_num'], ready['fps_den'])
        segment_count = math.ceil(ready['duration_s'] * float(fps) / ready['gop_frames'] - 1e-9)
        while (request := control.next_seek()) is not None:
            segment, run = request
            if segment >= segment_count:
                print('VSR COMMAND ERROR: Segment outside source duration', file=sys.stderr, flush=True)
                continue
            current = argparse.Namespace(**vars(args))
            current.start_time = segment * ready['gop_frames'] / float(fps)
            current.seconds = 0
            current.legacy_schedule = 'serial'
            base = args.output.rsplit('/', 2)[0]
            current.output = f'{base}/{run}/index.m3u8'
            print(json.dumps({'event': 'seek_started', 'run': run, 'segment': segment,
                              'media_time': current.start_time, 'monotonic_s': time.monotonic()}), flush=True)
            try:
                run_worker(current, retained=retained, control=control, run_id=run, start_segment=segment)
            except RunCancelled:
                pass
            except (OSError, RuntimeError):
                if not control.cancelled(run):
                    raise
    finally:
        control.accept({'cmd': 'stop'})
        if retained.get('effect') is not None:
            import torch
            torch.cuda.synchronize()
            retained['effect'].close()
