# Making a release

For 1.4.2, build Windows packages with `Build-Installer.ps1` and Linux packages with `python3 tools/build_linux.py`. Run the checks in [development](development.md), including package checks after both builds.

## Release files

| File | Purpose |
| --- | --- |
| `VSR-Everywhere-1.4.2-Setup.exe` | Windows wizard. Put this download first in the release notes. |
| `VSR-Everywhere-1.4.2-Windows.zip` | Payload for the PowerShell installer. |
| `Install-VSREverywhere.ps1` | Must match the Windows ZIP from the same build. |
| `VSR-Everywhere-1.4.2-plugin.zip` | Manual deployment with an existing runtime. |
| `VSR-Everywhere-1.4.2-Source.zip` | Source, documentation, and comparison images. |
| `Install-VSREverywhere-1.4.2-Linux.sh` | Linux systemd server installer. |
| `VSR-Everywhere-1.4.2-Linux.tar.gz` | Linux installer and guides. |
| `VSR-Everywhere-1.4.2-Linux-SHA256SUMS.txt` | Linux package hashes. |
| `VSR-Everywhere-1.4.2-SHA256SUMS.txt` | Hashes for the release downloads. |
| `LICENSE` | Project MIT license; also included in packages. |

Keep the plugin version, installer versions, startup checks, tests, download links, and changelog aligned. Keep the plugin ID unchanged so upgrades retain settings. Cache ownership markers describe the cache format and do not need to change with the release version.

The settings-page updater reads GitHub's latest stable release and its SHA-256 digest for the `-plugin.zip` asset. Keep that asset name unchanged. Packages include `update/windows.json`, `update/linux.json`, and `update/linux-requirements.txt`; both builders copy these from the installer dependency locks. The plugin embeds the same locks and ignores only their top-level product version when comparing runtime requirements. Runtime changes require the full installer. Run the plugin and settings checks, which cover update authorization, verification, compatibility, retries, and restart handling.

## Publish

1. Review the code, docs, known issues, and license notices. The Windows installer is unsigned unless signed separately.
2. Push the reviewed commit to `main` and wait for CI.
3. Create a draft `v1.4.2` release at that commit and upload the exact files from this build.
4. Check the uploaded names, sizes, and SHA-256 hashes against the local files.
5. Publish as a normal release and mark it Latest. Check the README download links.

Keep runtimes, build output, test jobs, source videos, and server settings out of Git. Binaries belong in release assets. Other downloaded components keep their own terms.

[Overview](../README.md) · [Changelog](../CHANGELOG.md)
