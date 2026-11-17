"""Verify that the distributable EXE, ZIP, script, and source package agree."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import zipfile


def entries(archive):
    result = {}
    for entry in archive.infolist():
        name = entry.filename.replace('\\', '/')
        assert name not in result, 'Duplicate archive path: ' + name
        assert 'HdrBridge' not in name and '0.2.7' not in name, 'Old product identity: ' + name
        result[name] = entry
    assert archive.testzip() is None, 'ZIP integrity failure'
    return result


root = Path(__file__).resolve().parents[2]
dist = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / 'dist'
license_text = (root / 'LICENSE').read_bytes()
bundle = (dist / 'VSR-Everywhere-1.4.0-Windows.zip').read_bytes()
wrapper = (dist / 'Install-VSREverywhere.ps1').read_bytes()
executable = (dist / 'VSR-Everywhere-1.4.0-Setup.exe').read_bytes()
assert sha256(bundle).hexdigest() in wrapper.decode('utf-8-sig'), 'Wrapper ZIP checksum mismatch'
assert bundle in executable and wrapper in executable, 'EXE embeds a different ZIP or script'
with zipfile.ZipFile(dist / 'VSR-Everywhere-1.4.0-Windows.zip') as archive:
    files = entries(archive)
    read = lambda name: archive.read(files[name])
    for name in ['LICENSE', 'plugin/LICENSE']:
        assert read(name) == license_text, 'Installer MIT license: ' + name
    metadata = json.loads(read('plugin/meta.json').decode('utf-8'))
    assert metadata['name'] == 'VSR Everywhere' and metadata['version'] == '1.4.0.0'
    assert metadata['targetAbi'] == '10.10.0.0', 'Jellyfin target ABI changed'
    locked = json.loads(read('downloads.lock.json').decode('utf-8-sig'))
    assert locked['targetAbi'] == metadata['targetAbi'], 'Installer and plugin target different Jellyfin ABIs'
    assert locked == json.loads((root / 'installer/downloads.lock.json').read_text(encoding='utf-8-sig'))
    for name in ['Installer.Core.psm1', 'Install-Engine.ps1', 'SetupWizard.ps1']:
        assert read(name).decode('utf-8-sig').replace('\r\n', '\n') == (root / 'installer' / name).read_text(encoding='utf-8-sig'), 'Stale installer: ' + name
    assert any(item['file'] == 'pynvvideocodec-2.2.3-cp314-cp314-win_amd64.whl' for item in locked['downloads'])
    for name, expected in json.loads(read('payload.sha256.json').decode('utf-8-sig')).items():
        assert sha256(read('plugin/' + name.replace('\\', '/'))).hexdigest() == expected, 'Payload hash: ' + name
    for source in (root / 'worker').glob('*.py'):
        assert read('plugin/worker/' + source.name) == source.read_bytes(), 'Stale worker: ' + source.name
    assert {name for name in files if name.startswith('plugin/worker/')} == {
        'plugin/worker/' + source.name for source in (root / 'worker').glob('*.py')
    }, 'Unexpected bundled worker component'
    assert read('plugin/Jellyfin.Plugin.VsrEverywhere.dll') == (dist / 'VSR-Everywhere/Jellyfin.Plugin.VsrEverywhere.dll').read_bytes()
with zipfile.ZipFile(dist / 'VSR-Everywhere-1.4.0-plugin.zip') as archive:
    files = entries(archive)
    assert {name for name in files if name.startswith('worker/') and not name.endswith('/')} == {
        'worker/' + source.name for source in (root / 'worker').glob('*.py')
    }, 'Unexpected manual plugin worker component'
    assert archive.read(files['LICENSE']) == license_text, 'Manual plugin MIT license'
    assert archive.read(files['THIRD_PARTY_NOTICES.md']) == (root / 'THIRD_PARTY_NOTICES.md').read_bytes(), 'Manual plugin third-party notices'
with zipfile.ZipFile(dist / 'VSR-Everywhere-1.4.0-Source.zip') as archive:
    files = entries(archive)
    for name in ['worker/vsr_gpu.py', 'worker/vsr_server.py', 'src/Jellyfin.Plugin.VsrEverywhere/BridgeSeek.cs',
                 'tools/compare_vsr_colour.py', 'installer/tests/Test-Package.py', 'installer/SetupWizard.ps1',
                 'installer/Installer.Core.psm1', 'installer/Install-Engine.ps1', 'installer/Install-VSREverywhere.ps1.in']:
        assert archive.read(files[name]) == (root / name).read_bytes(), 'Source package: ' + name
    assert not any(name.startswith('HANDOFF-') for name in files), 'Internal handoff note in source package'
    for source in [root / 'LICENSE', root / 'README.md', root / 'CHANGELOG.md', root / 'THIRD_PARTY_NOTICES.md', *sorted((root / 'docs').rglob('*'))]:
        if source.is_file():
            name = source.relative_to(root).as_posix()
            assert archive.read(files[name]) == source.read_bytes(), 'Missing or stale documentation: ' + name
print('PASS: package integrity, payload hashes, GPU dependency, current sources, branding, and matching embedded EXE/ZIP/script.')
