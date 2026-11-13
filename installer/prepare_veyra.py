"""Prepare the separately downloaded, checksum-locked Veyra runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile


def extract_runtime(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            if entry.is_dir():
                continue
            parts = entry.filename.replace('\\', '/').split('/')
            if parts.pop(0) != 'Veyra-2.1.5-NVIDIA-win64-portable':
                raise ValueError('Unexpected Veyra archive root')
            if not parts:
                continue
            selected = (parts[0] in ('runtime', 'shaders', 'licenses') or
                        len(parts) == 1 and (re.fullmatch(r'(avutil-61|swscale-10|vcruntime140(?:_1)?|msvcp140(?:_\d+)?|concrt140)\.dll', parts[0])
                                             or 'manifest' in parts[0] or parts[0] in ('LICENSE', 'THIRD_PARTY_NOTICES.md')))
            if not selected:
                continue
            target = destination.joinpath(*parts).resolve()
            if not target.is_relative_to(destination):
                raise ValueError('Veyra archive path escapes its private runtime')
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
    (destination / 'runtime_local').mkdir(exist_ok=True)
    for required in ('avutil-61.dll', 'swscale-10.dll', 'runtime/config/ngx-local.json',
                     'runtime/experimental/nr-ampere/nvngx_dlssnr.dll', 'shaders/ScaleBlit.dxil'):
        if not (destination / required).is_file():
            raise ValueError('Veyra runtime is incomplete: ' + required)


def main():
    parser = argparse.ArgumentParser()
    for option in ('manifest', 'downloads', 'target', 'component'):
        parser.add_argument('--' + option, type=Path, required=True)
    args = parser.parse_args()
    item = json.loads(args.manifest.read_text(encoding='utf-8-sig'))['veyra']
    archive = args.downloads / item['file']
    with archive.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != item['sha256']:
            raise ValueError('Veyra runtime checksum mismatch')
    extract_runtime(archive, args.target)
    for name in ('VsrVeyraFilter.exe', 'LICENSE', 'VsrVeyraFilter-source.zip'):
        shutil.copyfile(args.component / name, args.target / name)
    print('Prepared Veyra experimental neural-rendering runtime and GPL source.', flush=True)


if __name__ == '__main__':
    main()
