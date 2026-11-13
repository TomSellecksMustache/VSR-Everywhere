# VSR Everywhere 1.0.0 - Windows setup

## Requirements

- An initialized Jellyfin **12.1** server on this computer. Setup supports normal tray and Windows service installations.
- Windows 10 or 11 **x64**, a supported NVIDIA GPU with driver **570.65+**, or an Intel GPU with OpenVINO GPU access and HEVC Main 10 Quick Sync. Setup checks the actual GPU pipeline before installation.
- Internet access and **25 GB free** on a local drive of your choice for downloads, the private runtime, and setup temporary files. C: is not required for these large components. Playback uses RAM automatically; disk fallback uses the Jellyfin data drive.
- Permission to approve Windows administrator access.

Setup downloads Python, NumPy, PyAV, the video encoder, and the Microsoft Visual C++ runtime if needed. Detected NVIDIA systems receive PyTorch/CUDA and NVIDIA VFX; Intel systems receive OpenVINO and the public SR-1033 model. Both are provisioned on dual-vendor systems. It does not require system Python, the .NET SDK, or the CUDA Toolkit. It does not upgrade Jellyfin or change the graphics driver. New installs use Automatic; existing GPU overrides are retained. [GPU requirements and Intel validation status](docs/gpu-backends.md).

## Recommended: setup wizard

1. Download [**VSR-Everywhere-1.0.0-Setup.exe**](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.0.0/VSR-Everywhere-1.0.0-Setup.exe) to the Jellyfin server computer and open it.
2. Approve Windows administrator access.
3. Select **Next**, confirm the detected Jellyfin server, then select **Next**. Choose a download and runtime folder with 25 GB free, such as `G:\`, then select **Install**. Setup creates a `VsrEverywhere` subfolder there.
4. Wait for downloads and the video test. Setup briefly stops Jellyfin only after preparation succeeds, installs the plugin, restarts Jellyfin, and checks that the plugin loaded.
5. Select **Finish** to open VSR Everywhere settings in Jellyfin. Sign in to Jellyfin if asked.

New installations default to 4K processing and delivery, MPEG-TS/AAC, balanced quality, one concurrent session, and VSR enabled for all devices. To restrict devices or change resolution and quality, open the plugin settings. A 4K playback device should use 4K delivery even if processing is higher.

For a custom installation, choose **Choose a custom Jellyfin location** and browse to its data folder and `jellyfin.exe`. The data folder must contain `config\system.xml`. Setup does not guess between missing or invalid server folders.

## ZIP and PowerShell option

Download these two matching files into the same folder:

- [**VSR-Everywhere-1.0.0-Windows.zip**](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.0.0/VSR-Everywhere-1.0.0-Windows.zip)
- [**Install-VSREverywhere.ps1**](https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v1.0.0/Install-VSREverywhere.ps1)

Do not extract the ZIP. Right-click the PowerShell file and select **Run with PowerShell**, or run:

```powershell
powershell.exe -NoProfile -Sta -ExecutionPolicy Bypass -File .\Install-VSREverywhere.ps1
```

This opens the same setup wizard. The script verifies the ZIP before opening it.

To preselect G: while keeping the wizard:

```powershell
powershell.exe -NoProfile -Sta -ExecutionPolicy Bypass -File .\Install-VSREverywhere.ps1 -InstallFolder 'G:\'
```

For unattended installation on a computer with exactly one detected server:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-VSREverywhere.ps1 -Silent
```

Custom unattended installation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-VSREverywhere.ps1 -Silent -DataPath 'D:\JellyfinData' -ServerPath 'C:\Program Files\Jellyfin\Server\jellyfin.exe'
```

## What setup changes

- Installs the plugin under the detected data folder's `plugins\VSR-Everywhere` directory.
- Writes `plugins\configurations\Jellyfin.Plugin.VsrEverywhere.xml`, carrying forward saved quality, resolution, cache policy, and device selections where available.
- Places its private runtime, video encoder, verified downloads, setup logs, and setup temporary files in a `VsrEverywhere` subfolder of your selected folder. The default remains `%ProgramData%\VsrEverywhere`; `-InstallFolder` also works with `-Silent`.
- Keeps the small bootstrap payload under `%ProgramData%\VsrEverywhere\setup`. Windows runtime components may still use some system-drive space.
- Defaults to a shared RAM cache sized to the smaller of 7 GB or 60% of available physical RAM. No RAM-disk installation or cache-path setup is needed. `<Jellyfin data>\cache\vsr-everywhere` remains available for disk fallback.
- Places plugin/configuration backups outside the plugins directory at `<Jellyfin data>\vsr-everywhere-backups`.

Setup identifies the plugin by its stable ID and handles settings without requiring users to locate configuration files. It does not copy personal server data, passwords, or library contents into the download package. Downloads are verified by SHA-256; the Microsoft runtime is verified by its Microsoft Authenticode signature.

For an upgrade, select the same download/runtime folder used before. Setup can reuse a runtime whose verified dependency manifest differs only in the product version. A fresh runtime still requires 25 GB free, and the GPU/video checks run on every installation.

## Troubleshooting

If Windows asks whether to run the setup application, use the file obtained from the publisher. This build is unsigned; Windows SmartScreen may show an unfamiliar-app prompt. Signing the release with a publisher certificate is recommended for public distribution.

If hardware or the video test fails, setup stops before replacing the plugin. Install a compatible NVIDIA driver or choose supported settings, then run setup again. Interrupted downloads are verified and reused on the next attempt when complete.

If installation or startup verification fails, setup attempts to restore the plugin and configuration and restart the server. Keep the setup log and backup directory if manual recovery is needed. The **Open setup log** link on the final page shows details. Rollback cannot undo separately installed Microsoft runtime updates.

Higher-than-4K delivery requires a compatible GPU encoder and playback device. Memory mode serves a full seekable playlist and regenerates evicted segments on demand. Its budget covers all sessions, uploads, and active reads. Disk mode retains the existing full-playback or rolling-window options and disk-space limits. NVENC lookahead uses the compatible FFmpeg encoding path.

## Manual plugin installation

The separately built `VSR-Everywhere-1.0.0-plugin.zip` contains only the plugin files. It is intended for administrators who already have a working runtime. The wizard is the recommended route for new users.
