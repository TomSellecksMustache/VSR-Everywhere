# Building and testing

## Layout

| Path | Contents |
| --- | --- |
| `src/Jellyfin.Plugin.VsrEverywhere/` | Jellyfin integration, settings, sessions, HLS endpoints, and memory cache. |
| `worker/` | Video processing, encoding, startup, and interpolation. |
| `installer/` | Windows and Linux setup code and dependency locks. |
| `tests/` | Worker, settings, cache, and playback checks. |
| `tools/` | Package builders and comparison tools. |
| `dist/` | Generated release files, excluded from Git. |

The direct path is decode/upload, VSR, resize/color conversion, optional HDR/interpolation, NVENC, then HLS with AAC audio. Linux omits the Windows enhancement stages. Lookahead above 0 uses FFmpeg encoding with frame transfers through RAM. Compressed HLS segments use the shared RAM cache or disk fallback.

## Windows build

Use Windows x64, .NET 10 SDK (the plugin targets .NET 8), Git and internet access.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Build-Installer.ps1
```

This builds the plugin, EXE, Windows ZIP, PowerShell entry point, plugin ZIP, and source ZIP in `dist/`. It does not install anything into Jellyfin. `Build-Windows.ps1` builds only the plugin package. `Build-Installer.ps1 -SkipPluginBuild` reuses the plugin output, so only use it after building the current plugin.

The Windows dependency lock is `installer/downloads.lock.json`.

## Linux build

With Python 3 and .NET 10 SDK:

```bash
python3 tools/build_linux.py
python3 installer/linux/test_package.py
```

The output is a terminal installer, a tarball with the same installer and guides, and checksums in `dist/`.

## Checks

Use Python with the worker dependencies, Node.js, and .NET 10:

```powershell
python -m unittest discover -s tests -p "test_*.py"
node --test tests/settings.test.cjs
dotnet run --project tests/PluginChecks -c Release -- python
powershell.exe -NoProfile -ExecutionPolicy Bypass -File installer/tests/Test-Installer.ps1
python installer/tests/Test-Package.py
```

Run package checks after building. They compare embedded and standalone payloads, verify hashes and versions, and check current sources and documentation. Installer fixtures cover discovery, custom folders, settings, upgrades, startup checks, cancellation, and rollback.

Plugin checks accept `-p:JellyfinVersion=10.10.0`, `10.11.5`, or `12.1.0` to exercise the actual API assemblies. The device lookup adapter avoids a database-entity namespace change without replacing registered-token authorization.

The Linux CI runs installer, plugin/cache, and package checks on Ubuntu 24.04 and Rocky Linux 9. GPU playback checks need real hardware and are separate from CI. Stills do not prove motion quality, and short playback tests do not prove long-session stability or universal client support. Clean-machine installation and long-duration A/V drift still need broader testing.

[Overview](../README.md) · [Making a release](releasing.md)
