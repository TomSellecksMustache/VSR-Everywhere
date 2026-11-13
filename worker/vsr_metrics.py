"""Small, dependency-free stage counters and Windows process measurements."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import os
import threading
import time


class StageMetrics:
    def __init__(self):
        self._totals = defaultdict(float)
        self._counts = defaultdict(int)
        self._pending = threading.local()
        self._lock = threading.Lock()

    def add(self, stage, milliseconds):
        with self._lock:
            self._totals[stage] += milliseconds
            self._counts[stage] += 1

    @contextmanager
    def cpu(self, stage):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add(stage, (time.perf_counter() - start) * 1000)

    @contextmanager
    def gpu(self, stage, torch):
        # No per-stage synchronization: resolve once the frame has completed.
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        try:
            yield
        finally:
            end.record()
            if not hasattr(self._pending, "events"):
                self._pending.events = []
            self._pending.events.append((stage, start, end))

    def take_events(self):
        events = getattr(self._pending, "events", [])
        self._pending.events = []
        return events

    def resolve(self, events=None):
        for stage, start, end in self.take_events() if events is None else events:
            end.synchronize()
            self.add(stage, start.elapsed_time(end))

    def means(self):
        with self._lock:
            return {name: round(total / self._counts[name], 4)
                    for name, total in self._totals.items() if self._counts[name]}


def process_resources(process=None):
    """CPU seconds, peak working set, and current private bytes, never estimated."""
    if os.name != "nt":
        import resource
        if process is not None:
            return {}
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return {"cpu_seconds": usage.ru_utime + usage.ru_stime,
                "peak_working_set_bytes": usage.ru_maxrss * 1024}
    import ctypes
    from ctypes import wintypes

    class Memory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = int(process._handle) if process is not None else kernel.GetCurrentProcess()
    times = [wintypes.FILETIME() for _ in range(4)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    result = {}
    if kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        result["cpu_seconds"] = sum((t.dwHighDateTime << 32) + t.dwLowDateTime for t in times[2:]) / 10_000_000
    memory = Memory()
    memory.cb = ctypes.sizeof(memory)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    if psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
        result.update(peak_working_set_bytes=memory.PeakWorkingSetSize,
                      working_set_bytes=memory.WorkingSetSize, private_bytes=memory.PrivateUsage)
    return result
