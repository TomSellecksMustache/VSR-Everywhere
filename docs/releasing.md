# Making a release

For 1.5.0, build Windows packages with `Build-Installer.ps1` and Linux packages with `python3 tools/build_linux.py`. Run the checks in [development](development.md), including package checks after both builds.

## Release files

| File | Purpose |
| --- | --- |
| `VSR-Everywhere-1.5.0-Setup.exe` | Windows wizard. Put this download first in the release notes. |
| `VSR-Everywhere-1.5.0-Source.zip` | Source, documentation, and comparison images. |
| `Install-VSREverywhere-1.5.0-Linux.sh` | Linux systemd server installer. |
| `VSR-Everywhere-1.5.0-Linux.tar.gz` | Linux installer and guides. |

Publish only these four downloads. The builders also create internal payloads, a PowerShell entry point, a manual plugin ZIP, and checksum files for build verification. The Windows EXE embeds its payload; licenses remain included in the packages.

Keep the plugin version, installer versions, startup checks, tests, download links, and changelog aligned. Keep the plugin ID unchanged so upgrades retain settings. Cache ownership markers describe the cache format and do not need to change with the release version.

The settings page detects the latest stable release and links to its installer when no verified `-plugin.zip` update asset is published. Optional plugin update packages use GitHub's SHA-256 digest and runtime metadata; their authorization, verification, compatibility, retries, and restart handling remain covered by plugin checks.

## Publish

1. Review the code, docs, known issues, and license notices. The Windows installer is unsigned unless signed separately.
2. Push the reviewed commit to `main` and wait for CI.
3. Create a draft `v1.5.0` release at that commit and upload the exact files from this build.
4. Check the uploaded names, sizes, and SHA-256 hashes against the local files.
5. Publish as a normal release and mark it Latest. Check the README download links.

Keep runtimes, build output, test jobs, source videos, and server settings out of Git. Binaries belong in release assets. Other downloaded components keep their own terms.

[Overview](../README.md) · [Changelog](../CHANGELOG.md)
