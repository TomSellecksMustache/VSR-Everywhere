"""Synthetic output checks need no NVIDIA libraries or GPU."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('runtime_check', ROOT / 'installer/Test-Runtime.py')
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class RuntimeImageChecks(unittest.TestCase):
    def test_color_pattern(self):
        runtime.verify_test_pixels(bytes([0, 10, 20, 255, 240, 200]) * 32)

    def test_black_output(self):
        with self.assertRaises(RuntimeError):
            runtime.verify_test_pixels(bytes(3 * 64))

    def test_constant_color_output(self):
        with self.assertRaises(RuntimeError):
            runtime.verify_test_pixels(bytes([255, 0, 0]) * 64)

    def test_missing_or_malformed_pixels(self):
        for pixels in (b'', b'\0', b'\0\1'):
            with self.assertRaises(RuntimeError):
                runtime.verify_test_pixels(pixels)


if __name__ == '__main__':
    unittest.main()
