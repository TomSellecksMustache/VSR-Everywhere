from fractions import Fraction
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
from vsr_everywhere import build_ffmpeg_command, validate_memory_output


class MemoryOutputChecks(unittest.TestCase):
    key = 'b' * 64
    path = '/jellyfin/VsrEverywhere/ingest/' + 'a' * 48 + '/index.m3u8'

    def test_only_loopback_ingest_urls_and_header_safe_keys_are_accepted(self):
        for host in ['http://127.0.0.1:8096', 'https://[::1]:8920']:
            validate_memory_output(host + self.path, self.key)
        for url in ['http://192.168.1.5:8096' + self.path, 'http://localhost:8096' + self.path,
                    'ftp://127.0.0.1:8096' + self.path, 'http://user@127.0.0.1:8096' + self.path,
                    'http://127.0.0.1' + self.path, 'http://127.0.0.1:8096' + self.path + '?x=1',
                    'http://127.0.0.1:8096' + self.path + '#x', 'http://127.0.0.1:8096/x/index.m3u8',
                    'http://127.0.0.1:8096/../' + self.path.lstrip('/'),
                    'http://127.0.0.1:8096/%2e%2e/' + self.path.lstrip('/'),
                    'http://127.0.0.1:8096/a%2fb/' + self.path.lstrip('/'),
                    'http://127.0.0.1:8096/\n' + self.path.lstrip('/')]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_memory_output(url, self.key)
        for key in ['', 'b' * 63, 'b' * 65, 'b' * 62 + '\r\n', 'z' * 64]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_memory_output('http://127.0.0.1:8096' + self.path, key)

    def test_http_hls_uploads_use_authenticated_urls_without_temporary_files(self):
        url = 'http://127.0.0.1:8096' + self.path
        for segment_type, extension in [('fmp4', 'm4s'), ('mpegts', 'ts')]:
            command = build_ffmpeg_command('ffmpeg', 'source.mp4', url, 3840, 2160, Fraction(24),
                                           20, segment_type, memory_output=True, ingest_key=self.key,
                                           rolling_rewind_minutes=10)
            self.assertEqual(command[-1], url)
            self.assertEqual(command[command.index('-hls_segment_filename') + 1],
                             url.rsplit('/', 1)[0] + '/seg_%06d.' + extension)
            self.assertEqual(command[command.index('-headers') + 1], 'X-Vsr-Key: ' + self.key + '\r\n')
            self.assertEqual(command[command.index('-method') + 1], 'PUT')
            self.assertEqual(command[command.index('-http_persistent') + 1], '1')
            self.assertEqual(command[command.index('-hls_flags') + 1], 'independent_segments+delete_segments')
            self.assertNotIn('-hls_playlist_type', command)


if __name__ == '__main__':
    unittest.main()
