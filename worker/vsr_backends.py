"""GPU discovery shared by setup and playback; never selects a CPU fallback."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def model_path():
    return Path(sys.prefix) / 'models/intel/single-image-super-resolution-1033.xml'


def command(arguments, timeout=10):
    return subprocess.run(arguments, capture_output=True, text=True, check=True, timeout=timeout,
                          creationflags=0x08000000 if os.name == 'nt' else 0).stdout


def hardware():
    devices = []
    try:
        for line in command(['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader']).splitlines():
            name, driver = line.rsplit(',', 1)
            devices.append(dict(backend='nvidia', name=name.strip(), driver=driver.strip()))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    if os.name == 'nt':
        try:
            names = command(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                             'Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }'])
            devices.extend(dict(backend='intel', name=name.strip()) for name in names.splitlines()
                           if 'intel' in name.lower())
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        for path in sorted(Path('/sys/class/drm').glob('renderD*/device/vendor')):
            try:
                if path.read_text().strip().lower() == '0x8086':
                    devices.append(dict(backend='intel', name='Intel GPU', render='/dev/dri/' + path.parents[1].name))
            except OSError:
                pass
    return devices


def intel_device(core):
    # OpenVINO can enumerate non-Intel OpenCL GPUs too. Those cannot use Quick Sync.
    for device in core.available_devices:
        if device == 'GPU' or device.startswith('GPU.'):
            if 'intel' in core.get_property(device, 'FULL_DEVICE_NAME').lower():
                return device
    raise RuntimeError('No Intel GPU is accessible to OpenVINO. Install the Intel graphics/compute driver and check service permissions.')


def qsv_device_args():
    if os.name != 'nt':
        for path in sorted(Path('/sys/class/drm').glob('renderD*/device/vendor')):
            try:
                if path.read_text().strip().lower() == '0x8086':
                    return ['-init_hw_device', 'vaapi=vsr_va:/dev/dri/' + path.parents[1].name,
                            '-init_hw_device', 'qsv=vsr_qsv@vsr_va', '-filter_hw_device', 'vsr_qsv']
            except OSError:
                pass
    return []


def select_backend(requested, candidates):
    requested = 'nvidia' if requested == 'vsr' else requested
    if requested not in ('auto', 'nvidia', 'intel'):
        raise ValueError('GPU backend must be auto, nvidia, or intel')
    order = ('nvidia', 'intel') if requested == 'auto' else (requested,)
    for backend in order:
        if any(item['backend'] == backend and item.get('available') for item in candidates):
            return backend
    details = '; '.join(f"{item['backend']}: {item.get('reason', 'unavailable')}" for item in candidates
                        if item['backend'] in order)
    raise RuntimeError('No supported GPU backend is ready. ' + details)


def probe(ffmpeg='ffmpeg'):
    candidates = []
    devices = hardware()
    try:
        encoders = command([ffmpeg, '-hide_banner', '-encoders'])
    except (OSError, subprocess.SubprocessError) as exc:
        encoders = ''
        encoder_error = str(exc)
    else:
        encoder_error = 'Required HEVC encoder is missing from FFmpeg'
    for backend, modules, encoder in [('nvidia', ('torch', 'nvvfx'), 'hevc_nvenc'),
                                      ('intel', ('openvino',), 'hevc_qsv')]:
        item = dict(backend=backend, available=False)
        try:
            if not any(d['backend'] == backend for d in devices):
                raise RuntimeError('GPU/driver not detected')
            missing = [name for name in modules if importlib.util.find_spec(name) is None]
            if missing:
                raise RuntimeError('Run setup to install: ' + ', '.join(missing))
            if not re.search(r'\b' + encoder + r'\b', encoders):
                raise RuntimeError(encoder_error)
            if backend == 'intel':
                if not model_path().is_file() or not model_path().with_suffix('.bin').is_file():
                    raise RuntimeError('Run setup to download the Intel super-resolution model')
                import openvino as ov
                core = ov.Core()
                device = intel_device(core)
                item.update(device=device, name=core.get_property(device, 'FULL_DEVICE_NAME'))
            else:
                item['name'] = next(d['name'] for d in devices if d['backend'] == backend)
            # An encoder listed by FFmpeg may still be unusable with this driver/account.
            command([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                     *(qsv_device_args() if backend == 'intel' else []),
                     '-f', 'lavfi', '-i', 'color=size=640x360:rate=24', '-frames:v', '1',
                     '-vf', 'format=p010le', '-c:v', encoder, '-profile:v', 'main10', '-f', 'null', '-'])
            item['available'] = True
        except (ImportError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            details = exc.stderr.strip()[-2000:] if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else ''
            item['reason'] = str(exc) + (': ' + details if details else '')
        candidates.append(item)
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('auto', 'nvidia', 'intel'), default='auto')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--hardware-only', action='store_true')
    args = parser.parse_args()
    if args.hardware_only:
        print(json.dumps(hardware()))
        return 0
    candidates = probe(args.ffmpeg)
    try:
        selected = select_backend(args.backend, candidates)
        print(json.dumps(dict(available=True, backend=selected, candidates=candidates)))
        return 0
    except RuntimeError as exc:
        print(json.dumps(dict(available=False, backend=None, candidates=candidates, reason=str(exc))))
        return 1


if __name__ == '__main__':
    sys.exit(main())
