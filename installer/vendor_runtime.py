"""Assemble an isolated runtime from verified wheels, without system Python or pip."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile


def extract(archive: Path, destination: Path, wheel: bool = False):
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            if entry.is_dir():
                continue
            parts = entry.filename.replace("\\", "/").split("/")
            base = destination / "Lib" / "site-packages" if wheel else destination
            if wheel and parts[0].endswith(".data"):
                section = parts[1]
                parts = parts[2:]
                base = {"purelib": destination / "Lib/site-packages",
                        "platlib": destination / "Lib/site-packages",
                        "scripts": destination / "Scripts",
                        "headers": destination / "Include",
                        "data": destination}[section]
            target = base.joinpath(*parts).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("Archive path escapes the private runtime")
            if os.name == "nt":
                target = Path("\\\\?\\" + str(target))
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(entry) as source, target.open("wb") as output:
                while data := source.read(1024 * 1024):
                    output.write(data)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--downloads", type=Path, required=True)
    p.add_argument("--target", type=Path, required=True)
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    for item in manifest["downloads"]:
        path = args.downloads / item["file"]
        digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ValueError(f"Checksum mismatch: {item['file']}")
        if item["kind"] == "python" and (args.target / "python.exe").exists():
            # The bootstrap interpreter is already running from this directory.
            continue
        print("Installing " + item["file"], flush=True)
        if item['kind'] == 'model':
            destination = args.target / 'models/intel' / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        else:
            extract(path, args.target, wheel=item["kind"] == "wheel")
    tag = "".join(manifest["pythonVersion"].split(".")[:2])
    (args.target / f"python{tag}._pth").write_text(
        f"python{tag}.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8")
    (args.target / "vsr-runtime.json").write_text(json.dumps({
        "product": "VSR Everywhere", "version": manifest["version"],
        "manifestSha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
