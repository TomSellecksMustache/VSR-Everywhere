# Windows setup

## Requirements

- Windows 11 x64 for the full feature set.
- An initialized Jellyfin server, running as a normal desktop installation or Windows service.
- A compatible NVIDIA RTX GPU and driver. Setup checks the actual processing and encoding path.
- Internet access and a local NTFS/ReFS folder with room for the runtime.
- Administrator access for installation.

Setup downloads Python, CUDA/PyTorch libraries, NVIDIA VFX, the video encoder, and the Microsoft video runtime when needed. System Python, a compiler, and the CUDA Toolkit are not required. Jellyfin and the graphics driver must already be installed.

## Install with the wizard

1. Download [VSR-Everywhere-1.4.0-Setup.exe](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.4.0/VSR-Everywhere-1.4.0-Setup.exe).
2. Open it and approve administrator access.
3. Confirm the detected Jellyfin server. For a custom location, select its data folder and `jellyfin.exe`.
4. Choose a folder for the runtime and downloads. Setup creates a `VsrEverywhere` subfolder there; the large downloads do not need to go on the system drive.
5. Select Install. Setup downloads and tests the runtime before briefly stopping Jellyfin, backing up the plugin, and installing it.
6. Finish and open the plugin settings in Jellyfin.

The EXE is unsigned, so Windows may show an unfamiliar-app prompt.

New installs use 4K processing and delivery, Streaming Medium, strength 0.75, 20 Mbps, p4, lookahead 0, MPEG-TS/AAC, automatic RAM caching, and one session. VSR is enabled for all devices. Automatic content selection and interpolation are enabled on Windows; Auto HDR is off.

For upgrades, choose the same runtime folder. Existing compatible settings and device selections are kept. Verified dependencies can be reused when only the product version changed. Video checks still run.

## PowerShell option

Download the matching [Windows ZIP](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.4.0/VSR-Everywhere-1.4.0-Windows.zip) and [PowerShell installer](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.4.0/Install-VSREverywhere.ps1) into one folder. Leave the ZIP intact, then run:

```powershell
powershell.exe -NoProfile -Sta -ExecutionPolicy Bypass -File .\Install-VSREverywhere.ps1
```

To select a runtime folder in advance:

```powershell
powershell.exe -NoProfile -Sta -ExecutionPolicy Bypass -File .\Install-VSREverywhere.ps1 -InstallFolder 'D:\Apps'
```

For unattended setup with one detected server, add `-Silent`. Custom unattended setup also accepts `-DataPath` and `-ServerPath`. The ZIP and script must come from the same build.

## Files and recovery

| Item | Location |
| --- | --- |
| Runtime, downloads, and setup jobs | `<selected folder>\VsrEverywhere\` |
| Small bootstrap payload | `%ProgramData%\VsrEverywhere\setup\` |
| Plugin | `<Jellyfin data>\plugins\VSR-Everywhere\` |
| Settings | `<Jellyfin data>\plugins\configurations\Jellyfin.Plugin.VsrEverywhere.xml` |
| Backups | `<Jellyfin data>\vsr-everywhere-backups\` |
| Disk cache fallback | `<Jellyfin data>\cache\vsr-everywhere\` |

There is no fixed free-space minimum. If storage fills up, setup reports the failure so you can free space or choose another folder. The plugin is built against the Jellyfin 10.10 API and .NET 8. Setup checks actual plugin loading; API compatibility checks cover 10.10, 10.11, and 12.1. Older or changed server APIs can still fail the startup check.

Some system-drive space is still needed for the bootstrap and Microsoft runtime. Downloads are hash-checked; the Microsoft runtime signature is checked too.

If deployment or startup checks fail, setup attempts to restore the previous plugin and settings and restart Jellyfin. Keep the setup log and backup record if recovery is needed. Microsoft runtime updates are separate from plugin rollback.

The [manual plugin ZIP](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.4.0/VSR-Everywhere-1.4.0-plugin.zip) is for an existing working runtime. Use the wizard for a new install.

[Settings](docs/configuration.md) · [Troubleshooting and removal](docs/troubleshooting.md)
