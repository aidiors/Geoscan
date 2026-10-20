"""OS process peak, cumulative within a benchmark worker, not Python allocations."""

import sys


def peak_rss_mib() -> float | None:
    if sys.platform == "win32":
        import ctypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("faults", ctypes.c_ulong),
                *[
                    (name, ctypes.c_size_t)
                    for name in (
                        "peak_working_set",
                        "working_set",
                        "peak_paged",
                        "paged",
                        "peak_nonpaged",
                        "nonpaged",
                        "pagefile",
                        "peak_pagefile",
                    )
                ],
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = ctypes.c_void_p
        read = ctypes.windll.psapi.GetProcessMemoryInfo
        read.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if not read(current(), ctypes.byref(counters), counters.cb):
            return None
        return round(counters.peak_working_set / 1024**2, 2)
    if sys.platform in ("linux", "darwin"):
        import resource

        scale = 1024**2 if sys.platform == "darwin" else 1024
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale, 2)
    return None
