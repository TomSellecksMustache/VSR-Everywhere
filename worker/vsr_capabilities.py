"""Query the actual codec wheel and hardware; importing the worker stays GPU-free."""
from __future__ import annotations

import importlib


def probe_codec():
    try:
        nvc = importlib.import_module("PyNvVideoCodec")
    except Exception as exc:
        return {"importable": False, "reason": f"{type(exc).__name__}: {exc}", "hevc_input_formats": []}
    result = {"importable": True, "version": str(getattr(nvc, "__version__", "unknown")),
              "hevc_input_formats": [], "capabilities_verified": False}
    try:
        try:
            caps = nvc.GetEncoderCaps(codec="hevc", gpuid=0)
        except TypeError:
            caps = nvc.GetEncoderCaps(codec="hevc", gpu_id=0)
        formats = ["NV12", "YUV420", "ARGB", "ABGR"]
        if caps.get("support_10bit_encode"):
            formats.append("P010")
        if caps.get("support_yuv444_encode"):
            formats.append("YUV444")
            if caps.get("support_10bit_encode"):
                formats.append("YUV444_16BIT")
        result.update(hevc_input_formats=formats, capabilities_verified=True,
                      caps={str(key): value for key, value in caps.items() if isinstance(value, (str, int, float, bool))})
    except Exception as exc:
        result["reason"] = f"Capability query failed: {type(exc).__name__}: {exc}"
    return result
