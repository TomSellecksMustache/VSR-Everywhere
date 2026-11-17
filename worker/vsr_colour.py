"""Limited-range BT.601/709 RGB-to-YUV math, shared by host and device checks."""
from __future__ import annotations


def frame_rgb(frame):
    """Keep higher-depth SDR samples until the enhancement model's input boundary."""
    import numpy as np
    bits = max((c.bits for c in getattr(getattr(frame, 'format', None), 'components', ())), default=8)
    if bits > 10:
        raise ValueError('Only 8-bit and 10-bit SDR sources are supported')
    high_depth = bits > 8
    return np.ascontiguousarray(frame.to_ndarray(format='rgb48le' if high_depth else 'rgb24')), 65535 if high_depth else 255


def coefficients(matrix):
    if matrix == "bt709":
        return 0.2126, 0.7152, 0.0722
    if matrix == "bt601":
        return 0.299, 0.587, 0.114
    if matrix == "bt2020":
        return 0.2627, 0.6780, 0.0593
    raise ValueError("Expected bt601, bt709, or bt2020 colour matrix")


def rgb_to_yuv_reference(rgb, bits=8, matrix="bt709"):
    import numpy as np
    if bits not in (8, 10):
        raise ValueError("Expected 8 or 10 bits")
    rgb = np.asarray(rgb, dtype=np.float64)
    kr, kg, kb = coefficients(matrix)
    y = rgb[..., 0] * kr + rgb[..., 1] * kg + rgb[..., 2] * kb
    cb = (rgb[..., 2] - y) / (2 * (1 - kb))
    cr = (rgb[..., 0] - y) / (2 * (1 - kr))
    scale = 1 << (bits - 8)
    return np.stack((16 + 219 * y, 128 + 224 * cb, 128 + 224 * cr), axis=-1) * scale


def rgb_to_yuv420(rgb, bits=8, matrix="bt709"):
    """CHW float RGB in [0,1] -> contiguous NV12 or MSB-aligned P010 on the same device."""
    import torch
    import torch.nn.functional as F
    if bits not in (8, 10) or rgb.ndim != 3 or rgb.shape[0] != 3:
        raise ValueError("Expected CHW RGB and 8 or 10 bits")
    height, width = rgb.shape[1:]
    if height % 2 or width % 2:
        raise ValueError("YUV 4:2:0 requires even dimensions")
    kr, kg, kb = coefficients(matrix)
    rgb = rgb.clamp(0, 1)
    y = rgb[0] * kr + rgb[1] * kg + rgb[2] * kb
    cb = (rgb[2] - y) / (2 * (1 - kb))
    cr = (rgb[0] - y) / (2 * (1 - kr))
    uv = F.avg_pool2d(torch.stack((cb, cr)), 2)
    scale = 1 << (bits - 8)
    luma = ((16 + 219 * y) * scale).round().clamp(16 * scale, 235 * scale)
    chroma = ((128 + 224 * uv) * scale).round().clamp(16 * scale, 240 * scale)
    dtype = torch.uint8 if bits == 8 else torch.uint16
    surface = torch.empty((height * 3 // 2, width), dtype=dtype, device=rgb.device)
    shift = 1 if bits == 8 else 64
    surface[:height].copy_((luma * shift).to(dtype))
    surface[height:, 0::2].copy_((chroma[0] * shift).to(dtype))
    surface[height:, 1::2].copy_((chroma[1] * shift).to(dtype))
    return surface


def sdr_to_hdr(rgb):
    """sRGB/BT.709 primaries to BT.2020/PQ, with SDR white at 203 nits."""
    import torch
    linear = torch.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055).pow(2.4))
    matrix = rgb.new_tensor([[.6274039, .3292830, .0433131],
                             [.0690973, .9195404, .0113623],
                             [.0163914, .0880133, .8955953]])
    wide = torch.einsum('ij,jhw->ihw', matrix, linear).clamp_(0, 1).mul_(203 / 10000)
    power = wide.pow(2610 / 16384)
    return ((3424 / 4096 + 2413 / 128 * power) / (1 + 2392 / 128 * power)).pow(2523 / 32)
