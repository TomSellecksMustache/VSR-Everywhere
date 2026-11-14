"""Build the portable plugin and a self-contained Linux terminal installer."""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import tarfile
import xml.etree.ElementTree as ET


def build(no_restore=False, output_directory=None):
    root = Path(__file__).resolve().parents[1]
    project = root / 'src/Jellyfin.Plugin.VsrEverywhere/Jellyfin.Plugin.VsrEverywhere.csproj'
    version = ET.parse(project).getroot().findtext('PropertyGroup/Version')
    command = ['dotnet', 'build', str(project), '-c', 'Release', '--no-incremental']
    if no_restore:
        command.append('--no-restore')
    else:
        command += ['--configfile', str(root / 'NuGet.Config')]
    subprocess.run(command, check=True)
    dll = project.parent / 'bin/Release/net8.0/Jellyfin.Plugin.VsrEverywhere.dll'
    linux = root / 'installer/linux'
    runtime = json.loads((linux / 'runtime.json').read_text())
    assert runtime['version'] == version and runtime['product'] == 'VSR Everywhere'
    files = {
        'install.py': (linux / 'install.py').read_bytes(),
        'requirements.lock.txt': (linux / 'requirements.lock.txt').read_bytes(),
        'runtime.json': (linux / 'runtime.json').read_bytes(),
        'Test-Runtime.py': (root / 'installer/Test-Runtime.py').read_bytes(),
        'INSTALL-LINUX.md': (root / 'INSTALL-LINUX.md').read_bytes(),
        'LICENSE': (root / 'LICENSE').read_bytes(),
        'THIRD_PARTY_NOTICES.md': (root / 'THIRD_PARTY_NOTICES.md').read_bytes(),
        'plugin/Jellyfin.Plugin.VsrEverywhere.dll': dll.read_bytes(),
        'plugin/LICENSE': (root / 'LICENSE').read_bytes(),
        'plugin/THIRD_PARTY_NOTICES.md': (root / 'THIRD_PARTY_NOTICES.md').read_bytes(),
    }
    files.update({'plugin/worker/' + path.name: path.read_bytes() for path in sorted((root / 'worker').glob('*.py'))})
    metadata = dict(category='General', guid='7ca129d0-7454-41c7-aad6-55250b1a7b5b', name='VSR Everywhere',
                    version=version + '.0', status='Active', targetAbi='10.10.0.0',
                    description='NVIDIA VSR with GPU processing, RAM caching, and independent processing/delivery resolutions.')
    files['plugin/meta.json'] = json.dumps(metadata, indent=2).encode('utf-8')
    files['payload.sha256.json'] = json.dumps({name: sha256(data).hexdigest() for name, data in files.items()}, indent=2).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, data in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(data))
    payload = buffer.getvalue()
    shell = (linux / 'Install-VSREverywhere.sh.in').read_text(encoding='utf-8')
    for key, value in {'PAYLOAD_SHA256': sha256(payload).hexdigest(), 'PYTHON_VERSION': runtime['python']['version'],
                       'PYTHON_URL': runtime['python']['url'], 'PYTHON_SHA256': runtime['python']['sha256']}.items():
        shell = shell.replace('__' + key + '__', value)
    assert '__PAYLOAD_SHA256__' not in shell
    script_name = f'Install-VSREverywhere-{version}-Linux.sh'
    script = shell.encode('utf-8') + base64.encodebytes(payload)
    dist = Path(output_directory).resolve() if output_directory else root / 'dist'
    if not dist.is_relative_to((root / 'dist').resolve()):
        raise ValueError('Build output must stay inside this project dist folder')
    dist.mkdir(parents=True, exist_ok=True)
    (dist / script_name).write_bytes(script)
    (dist / script_name).chmod(0o755)
    archive_name = f'VSR-Everywhere-{version}-Linux.tar.gz'
    with tarfile.open(dist / archive_name, 'w:gz') as archive:
        for name, data, mode in [(script_name, script, 0o755), ('INSTALL-LINUX.md', files['INSTALL-LINUX.md'], 0o644),
                                 ('LICENSE', files['LICENSE'], 0o644), ('THIRD_PARTY_NOTICES.md', files['THIRD_PARTY_NOTICES.md'], 0o644)]:
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), mode
            archive.addfile(entry, io.BytesIO(data))
    sums = ''.join(sha256((dist / name).read_bytes()).hexdigest() + '  ' + name + '\n' for name in (script_name, archive_name))
    (dist / f'VSR-Everywhere-{version}-Linux-SHA256SUMS.txt').write_text(sums, encoding='ascii')
    print(f'Built: {dist / script_name}\nBuilt: {dist / archive_name}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-restore', action='store_true')
    parser.add_argument('--output-directory')
    options = parser.parse_args()
    build(options.no_restore, options.output_directory)
