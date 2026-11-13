"""Maintainer verification: download and assemble the same runtime as end-user setup."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
from vendor_runtime import extract

root = Path(__file__).resolve().parent
manifest = json.loads((root / "downloads.lock.json").read_text(encoding="utf-8"))
cache = root / "build" / "downloads"
cache.mkdir(parents=True, exist_ok=True)
for item in manifest["downloads"]:
    target = cache / item["file"]
    if not target.exists() or hashlib.file_digest(target.open("rb"), "sha256").hexdigest() != item["sha256"]:
        partial = target.with_suffix(target.suffix + ".part")
        print("Downloading " + item["file"], flush=True)
        with urllib.request.urlopen(item["url"], timeout=120) as response, partial.open("wb") as output:
            while data := response.read(4 * 1024 * 1024):
                output.write(data)
        assert hashlib.file_digest(partial.open("rb"), "sha256").hexdigest() == item["sha256"], item["file"]
        partial.replace(target)
    print("Verified " + item["file"], flush=True)
runtime = root / "build" / "test-runtime"
extract(cache / manifest["downloads"][0]["file"], runtime)
subprocess.run([str(runtime / "python.exe"), str(root / "vendor_runtime.py"),
                "--manifest", str(root / "downloads.lock.json"), "--downloads", str(cache),
                "--target", str(runtime)], check=True)
print("Private runtime ready: " + str(runtime), flush=True)
