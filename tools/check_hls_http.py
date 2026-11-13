"""Check this FFmpeg build's authenticated HLS PUT/DELETE support without a GPU."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading


def check(ffmpeg, segment_type, out):
    files, requests = {}, []
    key, token = 'b' * 64, 'a' * 48
    prefix = f'/VsrEverywhere/ingest/{token}/'

    class Receiver(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def handle(self):
            try:
                super().handle()
            except ConnectionResetError:
                pass  # FFmpeg closes its persistent connections when muxing ends.

        def receive(self):
            authenticated = self.headers.get('X-Vsr-Key') == key
            requests.append({'method': self.command, 'path': self.path, 'authenticated': authenticated})
            if not authenticated or not self.path.startswith(prefix):
                self.send_error(403)
                return
            name = self.path[len(prefix):]
            if not re.fullmatch(r'index\.m3u8|init\.mp4|seg_\d{6}\.(?:ts|m4s)', name):
                self.send_error(404)
                return
            if self.command == 'DELETE':
                files.pop(name, None)
            else:
                body = bytearray()
                if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
                    while True:
                        size = int(self.rfile.readline().split(b';', 1)[0], 16)
                        if not size:
                            while self.rfile.readline() not in (b'\r\n', b'\n', b''):
                                pass
                            break
                        if len(body) + size > 64 * 1024 * 1024:
                            self.send_error(413)
                            return
                        body.extend(self.rfile.read(size))
                        if self.rfile.read(2) != b'\r\n':
                            self.send_error(400)
                            return
                else:
                    size = int(self.headers.get('Content-Length', '0'))
                    if size > 64 * 1024 * 1024:
                        self.send_error(413)
                        return
                    body.extend(self.rfile.read(size))
                files[name] = bytes(body)
            self.send_response(201 if self.command == 'PUT' else 204)
            self.send_header('Content-Length', '0')
            self.end_headers()

        do_PUT = do_DELETE = receive

    server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}{prefix}'
    suffix = 'm4s' if segment_type == 'fmp4' else 'ts'
    command = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'warning',
               '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24:duration=12',
               '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=12',
               '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '48', '-sc_threshold', '0',
               '-c:a', 'aac', '-shortest', '-f', 'hls', '-hls_time', '2', '-hls_list_size', '2',
               '-hls_flags', 'independent_segments+delete_segments', '-hls_segment_type', segment_type,
               '-method', 'PUT', '-http_persistent', '1', '-headers', f'X-Vsr-Key: {key}\r\n',
               '-hls_segment_filename', url + f'seg_%06d.{suffix}']
    if segment_type == 'fmp4':
        command += ['-hls_fmp4_init_filename', 'init.mp4']
    command += [url + 'index.m3u8']
    try:
        result = subprocess.run(command, capture_output=True, timeout=45,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    (out / f'{segment_type}.stderr.log').write_bytes(result.stderr)
    (out / f'{segment_type}.requests.json').write_text(json.dumps(requests, indent=2), encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'{segment_type}: FFmpeg failed; inspect {out / (segment_type + ".stderr.log")}')
    assert requests and all(request['authenticated'] for request in requests), 'Authentication header missing'
    assert any(request['method'] == 'DELETE' for request in requests), 'No HTTP segment deletion observed'
    if segment_type == 'fmp4':
        assert files.get('init.mp4'), 'fMP4 init was not uploaded'
    playlist = files['index.m3u8'].decode('utf-8')
    names = [line for line in playlist.splitlines() if line and not line.startswith('#')]
    assert len(names) == 2 and all(name in files for name in names), 'Playlist references missing segments'
    return {'segment_type': segment_type, 'requests': len(requests),
            'deletes': sum(request['method'] == 'DELETE' for request in requests),
            'all_authenticated': True, 'retained_files': sorted(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report = [check(str(Path(args.ffmpeg).resolve()), kind, args.out) for kind in ('mpegts', 'fmp4')]
    (args.out / 'transport.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
