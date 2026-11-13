"""Check NVIDIA SDK imports using a file, avoiding Windows -c argument quoting."""
import sys
try:
    import av
    import nvvfx
    import torch
    print('PyTorch:', torch.__version__)
    print('PyAV:', av.__version__)
    print('CUDA:', torch.cuda.is_available())
    if not torch.cuda.is_available():
        print('CUDA unavailable to PyTorch; check NVIDIA driver and torch install.', file=sys.stderr)
        sys.exit(1)
    print('GPU:', torch.cuda.get_device_name(0))
    print('NVIDIA VFX import: OK')
except Exception as exc:
    print('NVIDIA VFX verification failed:', repr(exc), file=sys.stderr)
    raise
