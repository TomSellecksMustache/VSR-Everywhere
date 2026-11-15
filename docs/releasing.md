# Making a release

For 1.3.0, build Windows packages with `Build-Installer.ps1` and Linux packages with `python3 tools/build_linux.py`. Run the checks in [development](development.md), including package checks after both builds.

## Release files

| File | Purpose |
| --- | --- |
| `VSR-Everywhere-1.3.0-Setup.exe` | Windows wizard. Put this download first in the release notes. |
| `VSR-Everywhere-1.3.0-Windows.zip` | Payload for the PowerShell installer. |
| `Install-VSREverywhere.ps1` | Must match the Windows ZIP from the same build. |
| `VSR-Everywhere-1.3.0-plugin.zip` | Manual deployment with an existing runtime. |
| `VSR-Everywhere-1.3.0-Source.zip` | Source, documentation, and comparison images. |
| `Install-VSREverywhere-1.3.0-Linux.sh` | Linux systemd server installer. |
| `VSR-Everywhere-1.3.0-Linux.tar.gz` | Linux installer and guides. |
| `VSR-Everywhere-1.3.0-Linux-SHA256SUMS.txt` | Linux package hashes. |
| `VSR-Everywhere-1.3.0-SHA256SUMS.txt` | Hashes for the release downloads. |
| `LICENSE` | Project MIT license; also included in packages. |

Keep the plugin version, installer versions, startup checks, tests, download links, and changelog aligned. Keep the plugin ID unchanged so upgrades retain settings. Cache ownership markers describe the cache format and do not need to change with the release version.

## Publish

1. Review the code, docs, known issues, and license notices. The Windows installer is unsigned unless signed separately.
2. Push the reviewed commit to `main` and wait for CI.
3. Create a draft `v1.3.0` release at that commit and upload the exact files from this build.
4. Check the uploaded names, sizes, and SHA-256 hashes against the local files.
5. Publish as a normal release and mark it Latest. Check the README download links.

Keep runtimes, build output, test jobs, source videos, and server settings out of Git. Binaries belong in release assets. The Veyra executable must retain its GPL license and corresponding source. Other downloaded components keep their own terms.

[Overview](../README.md) · [Changelog](../CHANGELOG.md)
