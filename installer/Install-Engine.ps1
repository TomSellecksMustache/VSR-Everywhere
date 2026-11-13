[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$JobPath)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -Global
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Global
Import-Module (Join-Path $PSScriptRoot 'Installer.Core.psm1') -Force
$mutex = New-Object Threading.Mutex($false, 'Global\VsrEverywhereSetup')
$locked = $false; $stopped = $false; $installed = $false; $target = $null
$result = @{ Success = $false; Cancelled = $false; Message = ''; BackupPath = ''; Url = ''; Restarted = $false }
try {
    $locked = $mutex.WaitOne(0)
    if (-not $locked) { throw 'Another VSR Everywhere setup is already running.' }
    if (-not (Test-SetupAdministrator)) { throw 'Run setup as administrator.' }
    $plan = Get-Content -LiteralPath (Join-Path $JobPath 'plan.json') -Raw | ConvertFrom-Json
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'downloads.lock.json') -Raw | ConvertFrom-Json
    if ($plan.Product -ne 'VSR Everywhere' -or $plan.Version -ne '1.0.0') { throw 'The setup plan does not match this release.' }
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This release requires Windows 10 or 11 on an x64 computer.' }
    Write-SetupStatus $JobPath 'prepare' 'Checking Jellyfin and NVIDIA hardware...' 2
    $target = Get-JellyfinTarget -DataPath $plan.DataPath -ServerPath $plan.ServerPath
    $result.Url = $target.Url
    $gpu = Get-NvidiaStatus
    Write-SetupLog $JobPath $gpu
    $snapshot = Get-PluginSnapshot $target.DataPath
    $payload = Join-Path $PSScriptRoot 'plugin'
    $hashes = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'payload.sha256.json') -Raw | ConvertFrom-Json
    foreach ($file in $hashes.PSObject.Properties) {
        $path = Assert-ChildPath $payload (Join-Path $payload $file.Name)
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine [string]$file.Value) { throw 'The plugin payload did not pass verification. Download setup again.' }
    }
    $folder = if ($plan.PSObject.Properties['InstallFolder']) { [string]$plan.InstallFolder } else { '' }
    $root = New-SetupStorageRoot $folder
    $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($root))
    $downloads = Join-Path $root 'downloads'
    $manifestPath = Join-Path $PSScriptRoot 'downloads.lock.json'
    $lockHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $runtime = Join-Path $root ('runtimes\1.0.0-' + $lockHash.Substring(0, 12))
    $marker = Join-Path $runtime 'vsr-runtime.json'
    $reuse = $false
    if (Test-Path -LiteralPath $marker) {
        $saved = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
        $reuse = $saved.manifestSha256 -eq $lockHash
    }
    if (-not $reuse) {
        foreach ($candidate in @(Get-ChildItem -LiteralPath (Join-Path $root 'runtimes') -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\d+\.\d+\.\d+-[0-9a-f]{12}$' } | Sort-Object LastWriteTimeUtc -Descending)) {
            $path = Assert-ChildPath $root $candidate.FullName
            if ((Test-Path -LiteralPath (Join-Path $path 'python.exe') -PathType Leaf) -and (Test-SetupRuntimeManifest (Join-Path $path 'vsr-runtime.json') $manifestPath)) {
                $runtime = $path; $reuse = $true; break
            }
        }
    }
    if (-not $reuse) {
        if ($drive.AvailableFreeSpace -lt 25GB) {
            throw ('A fresh installation needs 25 GB free on ' + $drive.Name + ' for downloads, the runtime, and temporary files. Available: ' + [Math]::Round($drive.AvailableFreeSpace / 1GB, 1) + ' GB. Run setup again and choose another folder.')
        }
        $index = 0
        foreach ($item in $manifest.downloads) {
            Write-SetupStatus $JobPath 'download' ('Downloading ' + $item.file + '...') (5 + [int](50 * $index / $manifest.downloads.Count))
            $null = Get-VerifiedDownload $item $downloads $JobPath
            $index++
        }
        Write-SetupStatus $JobPath 'runtime' 'Preparing the private VSR runtime...' 57
        $staging = Assert-ChildPath $root (Join-Path $root ('runtimes\stage-' + [Guid]::NewGuid().ToString('N')))
        $pythonPackage = @($manifest.downloads | Where-Object { $_.kind -eq 'python' })[0]
        Expand-SetupArchive (Join-Path $downloads $pythonPackage.file) $staging
        $code = Invoke-SetupProcess (Join-Path $staging 'python.exe') @((Join-Path $PSScriptRoot 'vendor_runtime.py'), '--manifest', (Join-Path $PSScriptRoot 'downloads.lock.json'), '--downloads', $downloads, '--target', $staging) $JobPath 2400
        if ($code -ne 0) { throw 'The private runtime could not be prepared. Jellyfin has not been changed.' }
        if (Test-Path -LiteralPath $runtime) {
            $null = Assert-ChildPath $root $runtime
            Move-Item -LiteralPath $runtime -Destination ($runtime + '-incomplete-' + [Guid]::NewGuid().ToString('N'))
        }
        $null = Assert-ChildPath $root $runtime
        Move-Item -LiteralPath $staging -Destination $runtime
    }
    Write-SetupStatus $JobPath 'runtime' 'Checking the Microsoft video runtime...' 62
    Install-VsrCppRuntime $manifest $downloads $JobPath
    Write-SetupStatus $JobPath 'download' 'Downloading the video encoder...' 66
    $ffmpegArchive = Get-VerifiedDownload $manifest.ffmpeg $downloads $JobPath
    $encoder = Join-Path $root ('encoders\' + $manifest.ffmpeg.sha256.Substring(0, 12))
    if (-not (Test-Path -LiteralPath (Join-Path $encoder 'ffmpeg.exe'))) {
        $encoderStage = Assert-ChildPath $root (Join-Path $root ('encoders\stage-' + [Guid]::NewGuid().ToString('N')))
        Expand-SetupArchive $ffmpegArchive $encoderStage
        $ffmpegFile = @(Get-ChildItem -LiteralPath $encoderStage -Recurse -Filter ffmpeg.exe -File)
        $ffprobeFile = @(Get-ChildItem -LiteralPath $encoderStage -Recurse -Filter ffprobe.exe -File)
        if ($ffmpegFile.Count -ne 1 -or $ffprobeFile.Count -ne 1) { throw 'The encoder download did not contain the expected programs.' }
        $null = New-Item -ItemType Directory -Force -Path $encoder
        Copy-Item -LiteralPath $ffmpegFile[0].FullName -Destination (Join-Path $encoder 'ffmpeg.exe') -Force
        Copy-Item -LiteralPath $ffprobeFile[0].FullName -Destination (Join-Path $encoder 'ffprobe.exe') -Force
        # Keep the vendor's complete archive, license, and source notices for this separately downloaded component.
    }
    $cache = Join-Path $target.DataPath 'cache\vsr-everywhere'
    $null = Assert-ChildPath $target.DataPath $cache
    $null = New-Item -ItemType Directory -Force -Path $cache
    $account = if ($target.ServiceIdentity) { $target.ServiceIdentity } else { $target.OwnerSid }
    Grant-SetupAccess $runtime $account 'RX'
    Grant-SetupAccess $encoder $account 'RX'
    Grant-SetupAccess $cache $account 'M'
    $python = Join-Path $runtime 'python.exe'
    $ffmpeg = Join-Path $encoder 'ffmpeg.exe'; $ffprobe = Join-Path $encoder 'ffprobe.exe'
    $configuration = New-PluginConfiguration $snapshot.Config $python $ffmpeg $ffprobe $cache ([bool]$plan.AllDevices)
    Write-SetupStatus $JobPath 'test' 'Testing NVIDIA VSR, HEVC video, and audio...' 74
    $testArgs = @((Join-Path $PSScriptRoot 'Test-Runtime.py'), '--worker', (Join-Path $payload 'worker\vsr_everywhere.py'),
        '--ffmpeg', $ffmpeg, '--ffprobe', $ffprobe, '--report', (Join-Path $JobPath 'runtime-test.json'),
        '--quality', (Get-XmlValue $configuration 'VsrQuality'),
        '--processing-width', (Get-XmlValue $configuration 'ProcessingWidth'), '--processing-height', (Get-XmlValue $configuration 'ProcessingHeight'),
        '--stream-width', (Get-XmlValue $configuration 'StreamWidth'), '--stream-height', (Get-XmlValue $configuration 'StreamHeight'))
    if ((Invoke-SetupProcess $python $testArgs $JobPath 420) -ne 0) { throw 'The VSR or video encoding test failed. Jellyfin has not been changed. See the setup log for the exact cause.' }
    Test-SetupCancellation $JobPath
    # Refresh process/service state immediately before changing the selected server.
    $target = Get-JellyfinTarget -DataPath $target.DataPath -ServerPath $target.ServerPath
    Write-SetupStatus $JobPath 'install' 'Installing VSR Everywhere. Jellyfin will restart briefly...' 86
    $backup = Assert-ChildPath $target.DataPath (Join-Path $target.DataPath ('vsr-everywhere-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)))
    $result.BackupPath = $backup
    $stopped = $true
    Stop-SelectedJellyfin $target
    $verify = {
        try {
            $since = [DateTime]::UtcNow.AddSeconds(-1)
            Start-SelectedJellyfin $target
            Wait-VsrPlugin $target.DataPath $since
        } catch {
            $fresh = Get-JellyfinTarget -DataPath $target.DataPath -ServerPath $target.ServerPath
            Stop-SelectedJellyfin $fresh
            throw
        }
    }
    $null = Install-VsrPluginFiles $target.DataPath $payload $backup $configuration $verify
    $installed = $true; $stopped = $false
    $result.Success = $true; $result.Restarted = $true
    $result.Message = 'VSR Everywhere 1.0.0 is installed, tested, and loaded by Jellyfin.'
    Write-SetupStatus $JobPath 'done' $result.Message 100
} catch [OperationCanceledException] {
    $result.Cancelled = $true; $result.Message = $_.Exception.Message
    Write-SetupStatus $JobPath 'cancelled' $result.Message 0
} catch {
    $result.Message = $_.Exception.Message
    if ($stopped -and -not $installed -and $target -and $target.WasRunning) {
        try { Start-SelectedJellyfin $target; $result.Restarted = $true }
        catch { $result.Message += ' Jellyfin could not be restarted automatically. Start it from its service or tray app.' }
    }
    Write-SetupStatus $JobPath 'failed' $result.Message 0
    Write-SetupLog $JobPath ($_ | Out-String)
} finally {
    [IO.File]::WriteAllText((Join-Path $JobPath 'result.json'), ($result | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
if ($result.Success) { exit 0 }
if ($result.Cancelled) { exit 2 }
exit 1
