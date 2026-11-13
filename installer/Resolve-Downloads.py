"""Maintainer tool: lock official Windows x64 dependency URLs and SHA-256 hashes."""
from __future__ import annotations
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
PYTHON = "3.14.7"
PACKAGES = {
    "av": "18.1.0", "numpy": "2.4.6", "filelock": "4.2.4",
    "fsspec": "2026.9.0", "jinja2": "3.1.6", "markupsafe": "3.0.4",
    "mpmath": "1.3.0", "networkx": "3.6.1", "sympy": "1.14.0",
    "typing-extensions": "4.16.0", "setuptools": "80.9.0", "pynvvideocodec": "2.2.3",
    "openvino": "2026.1.0", "openvino-telemetry": "2025.2.0",
}

MODEL_BASE = 'https://storage.openvinotoolkit.org/repositories/open_model_zoo/2023.0/models_bin/1/single-image-super-resolution-1033/FP16/'
MODEL_FILES = {
    'single-image-super-resolution-1033.xml': '9af449041f28495cfbc346899dd2effb6b4de93bed420c01afc0c8e38d31b095',
    'single-image-super-resolution-1033.bin': '9a9d5a8cbd9520e4c282600feea95b31228d7de9716e8807acb53c572bd9cd31',
}

def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "VSR-Everywhere-Release/1.0.0"})
    return urllib.request.urlopen(request, timeout=120)

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(v for k, v in attrs if k == "href")

def official_wheel(index, filename):
    with fetch(index) as r:
        parser = Links()
        parser.feed(r.read().decode())
    matches = [urllib.parse.urljoin(index, link) for link in parser.links
               if urllib.parse.unquote(link.split("#")[0].split("/")[-1]) == filename]
    if len(matches) != 1:
        raise ValueError(f"Expected one official wheel for {filename}: {matches}")
    url, fragment = urllib.parse.urldefrag(matches[0])
    sha = urllib.parse.parse_qs(fragment)["sha256"][0]
    return {"file": filename, "url": url, "sha256": sha, "kind": "wheel"}

def main():
    downloads = []
    name = f"python-{PYTHON}-embed-amd64.zip"
    url = f"https://www.python.org/ftp/python/{PYTHON}/{name}"
    cache = ROOT / "build" / name
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        with fetch(url) as r:
            cache.write_bytes(r.read())
    downloads.append({"file": name, "url": url, "sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
                      "kind": "python", "size": cache.stat().st_size})
    for package, version in PACKAGES.items():
        with fetch(f"https://pypi.org/pypi/{package}/{version}/json") as r:
            metadata = json.load(r)
        candidates = []
        for wheel in metadata["urls"]:
            filename = wheel["filename"]
            if wheel["packagetype"] != "bdist_wheel" or wheel.get("yanked"):
                continue
            if filename.endswith("py3-none-any.whl") or filename.endswith("py2.py3-none-any.whl"):
                candidates.append((1, wheel))
            elif filename.endswith("win_amd64.whl"):
                if "-cp314-cp314-" in filename:
                    candidates.append((10, wheel))
                elif "-abi3-" in filename and any(f"-cp{v}-" in filename for v in [310, 311, 312, 313, 314]):
                    candidates.append((5, wheel))
        if not candidates:
            raise ValueError(f"No Python 3.14 Windows x64 wheel for {package} {version}")
        wheel = max(candidates, key=lambda pair: pair[0])[1]
        downloads.append({"file": wheel["filename"], "url": wheel["url"],
                          "sha256": wheel["digests"]["sha256"], "kind": "wheel", "size": wheel["size"]})
    downloads.append(official_wheel("https://download.pytorch.org/whl/cu128/torch/",
                                   "torch-2.11.0+cu128-cp314-cp314-win_amd64.whl"))
    downloads.append(official_wheel("https://pypi.nvidia.com/nvidia-vfx/",
                                   "nvidia_vfx-0.2.0.0-cp312-abi3-win_amd64.whl"))
    for item in downloads:
        if item['kind'] == 'wheel' and not item['file'].startswith(('av-', 'numpy-')):
            item['backends'] = ['intel' if item['file'].startswith(('openvino-', 'openvino_telemetry-')) else 'nvidia']
    downloads.extend(dict(file=name, url=MODEL_BASE + name, sha256=digest, kind='model', backends=['intel'])
                     for name, digest in MODEL_FILES.items())
    downloads.append(dict(file='LICENSE-OPENVINO-MODEL.txt', url='https://www.apache.org/licenses/LICENSE-2.0.txt',
                          sha256='cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
                          kind='model', backends=['intel']))
    result = {"product": "VSR Everywhere", "version": "1.0.0", "pythonVersion": PYTHON,
              "minimumDriver": "570.65", "targetAbi": "12.1.0.0", "downloads": downloads,
              "visualCppUrl": "https://aka.ms/vc14/vc_redist.x64.exe",
              "ffmpeg": {"file": "jellyfin-ffmpeg_8.1.3-1_portable_win64-clang-gpl.zip",
                         "url": "https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v8.1.3-1/jellyfin-ffmpeg_8.1.3-1_portable_win64-clang-gpl.zip",
                         "sha256": "3691f8b2d4c39c511b5faff1c8c4687bd782dac823e63ba69f0755f7ce1af9ce"}}
    (ROOT / "downloads.lock.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Locked {len(downloads)} official downloads for Python {PYTHON} Windows x64.")

if __name__ == "__main__":
    main()
