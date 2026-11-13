"""Verify the Linux installer and its embedded, current, MIT-licensed payload."""
import base64
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

root = Path(__file__).resolve().parents[2]
dist = root / 'dist'
script = dist / 'Install-VSREverywhere-1.0.0-Linux.sh'
raw = script.read_bytes()
header, encoded = raw.split(b'\n__VSR_PAYLOAD__\n', 1)
payload = base64.b64decode(encoded, validate=False)
assert sha256(payload).hexdigest().encode() in header, 'Embedded payload hash differs'
assert b'__PYTHON_' not in header and b'\r\n' not in header, 'Unresolved token or Windows line endings'
with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
    names = archive.getnames()
    assert len(names) == len(set(names))
    assert all(member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts for member in archive)
    read = lambda name: archive.extractfile(name).read()
    hashes = json.loads(read('payload.sha256.json'))
    assert set(names) == set(hashes) | {'payload.sha256.json'}
    for name, digest in hashes.items():
        assert sha256(read(name)).hexdigest() == digest, name
    for name, source in {'install.py': root / 'installer/linux/install.py',
                         'requirements.lock.txt': root / 'installer/linux/requirements.lock.txt',
                         'runtime.json': root / 'installer/linux/runtime.json',
                         'Test-Runtime.py': root / 'installer/Test-Runtime.py',
                         'INSTALL-LINUX.md': root / 'INSTALL-LINUX.md',
                         'LICENSE': root / 'LICENSE', 'plugin/LICENSE': root / 'LICENSE'}.items():
        assert read(name) == source.read_bytes(), 'Stale Linux source: ' + name
    for worker in (root / 'worker').glob('*.py'):
        assert read('plugin/worker/' + worker.name) == worker.read_bytes(), 'Stale worker: ' + worker.name
    assert json.loads(read('plugin/meta.json'))['version'] == '1.0.0.0'
    assert read('plugin/Jellyfin.Plugin.VsrEverywhere.dll') == (root / 'src/Jellyfin.Plugin.VsrEverywhere/bin/Release/net10.0/Jellyfin.Plugin.VsrEverywhere.dll').read_bytes()
with tarfile.open(dist / 'VSR-Everywhere-1.0.0-Linux.tar.gz', 'r:gz') as archive:
    member = archive.getmember(script.name)
    assert member.mode == 0o755
    assert archive.extractfile(member).read() == raw
    assert archive.extractfile('LICENSE').read() == (root / 'LICENSE').read_bytes()
bash = shutil.which('bash') or shutil.which('bash.exe')
if bash:
    subprocess.run([bash, '-n', script.as_posix()], check=True)
    result = subprocess.run([bash, script.as_posix(), '--help'], check=True, capture_output=True, text=True)
    assert 'VSR Everywhere 1.0.0' in result.stdout
for line in (dist / 'VSR-Everywhere-1.0.0-Linux-SHA256SUMS.txt').read_text().splitlines():
    expected, name = line.split('  ', 1)
    assert sha256((dist / name).read_bytes()).hexdigest() == expected
print('PASS: Linux script syntax/help, archive modes, embedded payload hashes, pinned runtime, current DLL/workers, MIT notices, and checksums.')
