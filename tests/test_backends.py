import sys
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
from vsr_backends import intel_device, probe, select_backend


class BackendTests(unittest.TestCase):
    def test_auto_prefers_ready_nvidia(self):
        candidates = [dict(backend='intel', available=True), dict(backend='nvidia', available=True)]
        self.assertEqual(select_backend('auto', candidates), 'nvidia')

    def test_auto_uses_intel_when_nvidia_unavailable(self):
        candidates = [dict(backend='nvidia', available=False), dict(backend='intel', available=True)]
        self.assertEqual(select_backend('auto', candidates), 'intel')

    def test_forced_backend_does_not_fall_back(self):
        candidates = [dict(backend='nvidia', available=True),
                      dict(backend='intel', available=False, reason='Intel driver missing')]
        with self.assertRaisesRegex(RuntimeError, 'Intel driver missing'):
            select_backend('intel', candidates)

    def test_no_ready_backend(self):
        with self.assertRaisesRegex(RuntimeError, 'No supported GPU backend'):
            select_backend('auto', [])

    def test_invalid_override(self):
        with self.assertRaises(ValueError):
            select_backend('amd', [])

    def test_legacy_alias(self):
        self.assertEqual(select_backend('vsr', [dict(backend='nvidia', available=True)]), 'nvidia')

    def test_only_intel_gpu_selected(self):
        core = Mock(available_devices=['CPU', 'GPU.0', 'GPU.1'])
        core.get_property.side_effect = ['NVIDIA GPU', 'Intel Arc GPU']
        self.assertEqual(intel_device(core), 'GPU.1')

    def test_non_intel_opencl_gpu_rejected(self):
        core = Mock(available_devices=['CPU', 'GPU'])
        core.get_property.return_value = 'NVIDIA GeForce RTX'
        with self.assertRaisesRegex(RuntimeError, 'No Intel GPU'):
            intel_device(core)

    def test_probe_reports_missing_runtime_without_importing_cuda(self):
        with patch('vsr_backends.hardware', return_value=[dict(backend='nvidia', name='RTX')]), \
             patch('vsr_backends.command', return_value='hevc_nvenc hevc_qsv'), \
             patch('vsr_backends.importlib.util.find_spec', return_value=None):
            candidates = probe()
        self.assertFalse(any(item['available'] for item in candidates))
        self.assertIn('Run setup', candidates[0]['reason'])

    def test_encoder_driver_failure_is_reported_without_crashing_auto(self):
        failure = subprocess.CalledProcessError(1, 'ffmpeg', stderr='Cannot load nvcuda.dll')
        with patch('vsr_backends.hardware', return_value=[dict(backend='nvidia', name='RTX')]), \
             patch('vsr_backends.command', side_effect=['hevc_nvenc hevc_qsv', failure]), \
             patch('vsr_backends.importlib.util.find_spec', return_value=object()):
            candidates = probe()
        self.assertFalse(candidates[0]['available'])
        self.assertIn('Cannot load nvcuda.dll', candidates[0]['reason'])


if __name__ == '__main__':
    unittest.main()
