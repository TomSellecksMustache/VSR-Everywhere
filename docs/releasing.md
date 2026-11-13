# Preparing a GitHub release

[Overview](../README.md) · [Documentation](README.md) · [Build instructions](development.md)

The local documentation uses repository-relative links and includes its comparison images. It does not require a particular GitHub username, repository name, branch, or external image host.

Repository: [TomSellecksMustache/VSR-Everywhere](https://github.com/TomSellecksMustache/VSR-Everywhere). Release downloads: [v1.0.0](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/tag/v1.0.0).

## Local release files

Build with `Build-Installer.ps1`, then check the outputs in `dist/`:

For the Linux server packages, run `python3 tools/build_linux.py` and verify them with `python3 installer/linux/test_package.py`.

| File | Audience |
| --- | --- |
| `VSR-Everywhere-1.0.0-Setup.exe` | Recommended single-file wizard. Downloads its larger dependencies. |
| `VSR-Everywhere-1.0.0-Windows.zip` | Companion payload for the standalone PowerShell installer. |
| `Install-VSREverywhere.ps1` | PowerShell entry point; must match the ZIP from the same build. |
| `VSR-Everywhere-1.0.0-plugin.zip` | Manual deployment for administrators with a working runtime. |
| `VSR-Everywhere-1.0.0-Source.zip` | Source, guides, and comparison images for review or rebuilding. |
| `Install-VSREverywhere-1.0.0-Linux.sh` | Standalone terminal installer for native Linux systemd services. |
| `VSR-Everywhere-1.0.0-Linux.tar.gz` | The Linux installer, setup guide, and license notices. |
| `VSR-Everywhere-1.0.0-Linux-SHA256SUMS.txt` | Checksums for the Linux installer and bundle. |
| `LICENSE` | MIT license for the project code; also included in the packages. |

The separate `VSR-Everywhere-Comparison-Shots.zip` contains the original 40 exported PNGs and can be attached as an additional release asset. The repository's gallery uses smaller WebP assets to keep the source tree manageable.

## Before publication

1. Review the README, comparison captions, source/input limits, and [1.0.0 release notes](../CHANGELOG.md).
2. Confirm that the packages include the project's MIT `LICENSE` and third-party notices. Third-party software and sample footage retain their respective rights.
3. Run the checks in [development](development.md), including package integrity after the build. Review the generated source archive before uploading it.
4. Confirm that the intended release artifacts match. The EXE and standalone PowerShell entry point must contain the same current payload.
5. Decide whether to sign the EXE with the publisher's certificate. The current build is unsigned.

## Publishing when ready

Push the reviewed source tree to the repository, then create a **v1.0.0** release at the reviewed commit and attach the intended files. Use the [changelog](../CHANGELOG.md) as the release description. Keep the README's download links aligned with the release tag and asset names.

Keep generated `dist/`, dependency runtimes, build directories, test work directories, internal handoff notes, source videos, and local server settings out of the Git history. `.gitignore` covers generated files inside this project. Release binaries belong in release assets rather than the source history.

## Testing channel

The `testing` branch carries automatic GPU detection and the experimental Intel OpenVINO/Quick Sync backend. Publish it as the **v1.0.0-testing.1** prerelease with **latest disabled**. Keep the existing **v1.0.0** release marked latest; its asset files and the default README download links stay unchanged. The main README links the prerelease below the stable downloads in **Intel/AMD-based upscaling (testing)**.

Include the matching Windows EXE, ZIP, and PowerShell installer, Linux installer and bundle, source, manual plugin package, MIT license, and checksums. Put the Windows EXE link before Linux in the release notes. The packaged plugin remains version 1.0.0; the testing tag identifies this separate build. State that Intel GPU playback needs hardware validation and AMD enhancement is not implemented.

These are maintainer instructions. Preparing this package does not create a remote repository, commit, tag, release, or push.
