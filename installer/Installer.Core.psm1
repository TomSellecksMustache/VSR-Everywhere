Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Global
$script:PluginId = '7ca129d0-7454-41c7-aad6-55250b1a7b5b'
$script:AssemblyFile = 'Jellyfin.Plugin.VsrEverywhere.dll'

function ConvertTo-NativeArgument([AllowEmptyString()][string]$Value) {
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    return '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Assert-ChildPath([string]$Root, [string]$Path) {
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) { throw 'Setup refused a path outside its destination folder.' }
    $cursor = $full
    while ($cursor.Length -ge $rootFull.TrimEnd('\').Length) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Setup does not install through symbolic links or junctions. Select a physical folder.'
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor -or [string]::IsNullOrEmpty($parent)) { break }
        $cursor = $parent
    }
    return $full
}

function Get-SetupStorageRoot([string]$Folder) {
    if ([string]::IsNullOrWhiteSpace($Folder)) { $Folder = $env:ProgramData }
    $Folder = [Environment]::ExpandEnvironmentVariables($Folder)
    if ($Folder -notmatch '^[A-Za-z]:[\\/]') { throw 'Choose an absolute folder on a local drive, such as G:\.' }
    $folderFull = [IO.Path]::GetFullPath($Folder)
    $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($folderFull))
    if (-not $drive.IsReady -or $drive.DriveType -ne 'Fixed' -or $drive.DriveFormat -notin @('NTFS', 'ReFS')) {
        throw 'Choose a local fixed drive with NTFS or ReFS permissions.'
    }
    return Assert-ChildPath $drive.RootDirectory.FullName (Join-Path $folderFull 'VsrEverywhere')
}

function New-SetupStorageRoot([string]$Folder) {
    $root = Get-SetupStorageRoot $Folder
    $null = New-Item -ItemType Directory -Force -Path $root
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($entry in @(@('S-1-5-32-544', 'FullControl'), @('S-1-5-18', 'FullControl'), @('S-1-5-32-545', 'ReadAndExecute'))) {
        $sid = New-Object Security.Principal.SecurityIdentifier($entry[0])
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, $entry[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $root -AclObject $acl
    return $root
}

function Get-XmlValue($Document, [string]$Name, [string]$Default = '') {
    if ($null -eq $Document) { return $Default }
    $node = $Document.DocumentElement.SelectSingleNode($Name)
    if ($null -eq $node) { return $Default }
    return $node.InnerText
}

function Get-PluginSnapshot([string]$DataPath) {
    $plugins = Join-Path $DataPath 'plugins'
    $found = @()
    if (Test-Path -LiteralPath $plugins) {
        foreach ($dir in Get-ChildItem -LiteralPath $plugins -Directory) {
            $metaPath = Join-Path $dir.FullName 'meta.json'
            if (-not (Test-Path -LiteralPath $metaPath -PathType Leaf)) { continue }
            try { $meta = Get-Content -LiteralPath $metaPath -Raw | ConvertFrom-Json } catch { continue }
            if ([string]$meta.guid -ne $script:PluginId) { continue }
            $found += $dir.FullName
        }
    }
    if ($found.Count -gt 1) { throw 'More than one copy of this plugin is installed. Remove the duplicate copy before running setup.' }
    $config = Join-Path $plugins 'configurations\Jellyfin.Plugin.VsrEverywhere.xml'
    if ($found.Count -eq 1 -and -not (Test-Path -LiteralPath $config)) {
        foreach ($dll in Get-ChildItem -LiteralPath $found[0] -Filter 'Jellyfin.Plugin.*.dll' -File) {
            $candidate = Join-Path $plugins ('configurations\' + $dll.BaseName + '.xml')
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $config = $candidate; break }
        }
    }
    $xml = $null
    if (Test-Path -LiteralPath $config -PathType Leaf) { $xml = [xml](Get-Content -LiteralPath $config -Raw) }
    return [pscustomobject]@{ PluginPath = $(if ($found.Count) { $found[0] } else { '' }); ConfigPath = $config; Config = $xml }
}

function Get-ArgumentValue([string]$CommandLine, [string]$Name) {
    $match = [regex]::Match($CommandLine, '(?:^|\s)' + [regex]::Escape($Name) + '(?:=|\s+)(?:"([^"]+)"|([^\s]+))', 'IgnoreCase')
    if ($match.Success) { if ($match.Groups[1].Success) { return $match.Groups[1].Value }; return $match.Groups[2].Value }
    return ''
}

function Get-JellyfinInstallations {
    $candidates = New-Object 'System.Collections.Generic.List[object]'
    $services = @(Get-CimInstance Win32_Service -Filter "Name LIKE '%jellyfin%'" -ErrorAction SilentlyContinue)
    $processes = @(Get-CimInstance Win32_Process -Filter "Name = 'jellyfin.exe'" -ErrorAction SilentlyContinue)
    foreach ($process in $processes) {
        $data = Get-ArgumentValue ([string]$process.CommandLine) '--datadir'
        if (-not $data) { $data = Get-ArgumentValue ([string]$process.CommandLine) '-d' }
        if ($data -and $process.ExecutablePath) { $candidates.Add([pscustomobject]@{ DataPath = $data; ServerPath = [string]$process.ExecutablePath }) }
    }
    foreach ($key in @('HKLM:\SOFTWARE\WOW6432Node\Jellyfin\Server', 'HKLM:\SOFTWARE\Jellyfin\Server', 'HKCU:\SOFTWARE\Jellyfin\Server')) {
        $settings = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if ($settings -and $settings.PSObject.Properties['DataFolder'] -and $settings.PSObject.Properties['InstallFolder']) {
            $candidates.Add([pscustomobject]@{ DataPath = [Environment]::ExpandEnvironmentVariables([string]$settings.DataFolder); ServerPath = Join-Path ([string]$settings.InstallFolder) 'jellyfin.exe' })
        }
    }
    $defaultServer = Join-Path $env:ProgramFiles 'Jellyfin\Server\jellyfin.exe'
    foreach ($data in @((Join-Path $env:LOCALAPPDATA 'Jellyfin'), (Join-Path $env:ProgramData 'Jellyfin\Server'), $env:JELLYFIN_DATA_DIR)) {
        if ($data) { $candidates.Add([pscustomobject]@{ DataPath = $data; ServerPath = $defaultServer }) }
    }
    $seen = @{}
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath (Join-Path $candidate.DataPath 'config\system.xml') -PathType Leaf)) { continue }
        if (-not (Test-Path -LiteralPath $candidate.ServerPath -PathType Leaf)) { continue }
        $key = [IO.Path]::GetFullPath($candidate.DataPath).ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        Get-JellyfinTarget -DataPath $candidate.DataPath -ServerPath $candidate.ServerPath -Services $services -Processes $processes
    }
}

function Get-JellyfinTarget([string]$DataPath, [string]$ServerPath, $Services = $null, $Processes = $null) {
    $DataPath = [IO.Path]::GetFullPath($DataPath).TrimEnd('\')
    $ServerPath = [IO.Path]::GetFullPath($ServerPath)
    if (-not (Test-Path -LiteralPath (Join-Path $DataPath 'config\system.xml') -PathType Leaf)) { throw 'Select the Jellyfin data folder containing config\system.xml.' }
    if ([IO.Path]::GetFileName($ServerPath) -ine 'jellyfin.exe' -or -not (Test-Path -LiteralPath $ServerPath -PathType Leaf)) { throw 'Select the Jellyfin Server executable (jellyfin.exe).' }
    $info = (Get-Item -LiteralPath $ServerPath).VersionInfo
    $version = '{0}.{1}.{2}' -f $info.FileMajorPart, $info.FileMinorPart, $info.FileBuildPart
    if ($info.FileMajorPart -ne 12 -or $info.FileMinorPart -ne 1) { throw "This release requires Jellyfin 12.1. The selected server is $version. Setup will not upgrade or replace your server." }
    if ($null -eq $Services) { $Services = @(Get-CimInstance Win32_Service -Filter "Name LIKE '%jellyfin%'" -ErrorAction SilentlyContinue) }
    if ($null -eq $Processes) { $Processes = @(Get-CimInstance Win32_Process -Filter "Name = 'jellyfin.exe'" -ErrorAction SilentlyContinue) }
    $service = $null
    foreach ($item in $Services) {
        $parameters = Get-ItemProperty -LiteralPath ('HKLM:\SYSTEM\CurrentControlSet\Services\' + $item.Name + '\Parameters') -ErrorAction SilentlyContinue
        $arguments = ''
        $application = ''
        if ($parameters) {
            if ($parameters.PSObject.Properties['AppParameters']) { $arguments = [string]$parameters.AppParameters }
            if ($parameters.PSObject.Properties['Application']) { $application = [string]$parameters.Application }
        }
        $serviceData = Get-ArgumentValue ($arguments + ' ' + $item.PathName) '--datadir'
        if (-not $serviceData) { $serviceData = Get-ArgumentValue ($arguments + ' ' + $item.PathName) '-d' }
        if (($serviceData -and [IO.Path]::GetFullPath($serviceData).TrimEnd('\') -ieq $DataPath) -or
            (-not $serviceData -and ($application -ieq $ServerPath -or [string]$item.PathName -like ('*' + $ServerPath + '*')))) { $service = $item; break }
    }
    $process = $null
    foreach ($item in $Processes) {
        if ([string]$item.ExecutablePath -ine $ServerPath) { continue }
        $processData = Get-ArgumentValue ([string]$item.CommandLine) '--datadir'
        if (-not $processData) { $processData = Get-ArgumentValue ([string]$item.CommandLine) '-d' }
        if ($processData -and [IO.Path]::GetFullPath($processData).TrimEnd('\') -ine $DataPath) { continue }
        $process = $item; break
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $arguments = '--datadir ' + (ConvertTo-NativeArgument $DataPath)
    if ($process) {
        $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction SilentlyContinue
        if ($owner -and $owner.ReturnValue -eq 0) { $identity = [string]$owner.Sid }
        $arguments = ([string]$process.CommandLine -replace '^\s*(?:"[^"]+"|\S+)\s*', '')
    }
    $serviceName = ''; $serviceIdentity = ''
    if ($service) { $serviceName = [string]$service.Name; $serviceIdentity = [string]$service.StartName }
    $port = '8096'; $base = ''
    $networkPath = Join-Path $DataPath 'config\network.xml'
    if (Test-Path -LiteralPath $networkPath) {
        $network = [xml](Get-Content -LiteralPath $networkPath -Raw)
        $port = Get-XmlValue $network 'InternalHttpPort' '8096'
        $base = (Get-XmlValue $network 'BaseUrl').Trim('/')
    }
    [pscustomobject]@{ DataPath = $DataPath; ServerPath = $ServerPath; Version = $version;
        ServiceName = $serviceName; ServiceIdentity = $serviceIdentity; OwnerSid = $identity;
        ProcessId = $(if ($process) { [int]$process.ProcessId } else { 0 });
        WasRunning = [bool]($process -or ($service -and $service.State -eq 'Running'));
        Arguments = $arguments; Url = ('http://localhost:' + $port + $(if ($base) { '/' + $base } else { '' }));
        DisplayName = ('Jellyfin ' + $version + ' - ' + $DataPath) }
}

function Get-NvidiaStatus {
    $command = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    $path = if ($command) { $command.Source } else { Join-Path $env:WINDIR 'System32\nvidia-smi.exe' }
    if (-not (Test-Path -LiteralPath $path)) { throw 'An NVIDIA RTX GPU and a compatible NVIDIA driver are required. Install the NVIDIA driver, then run setup again.' }
    $lines = @(& $path '--query-gpu=name,driver_version' '--format=csv,noheader' 2>$null)
    if ($LASTEXITCODE -ne 0 -or $lines.Count -eq 0) { throw 'Windows could not access the NVIDIA GPU. Install its driver and run setup again.' }
    $values = $lines[0].Split(',')
    if ($values.Count -ne 2 -or [version]$values[1].Trim() -lt [version]'570.65') { throw 'The NVIDIA driver must be version 570.65 or newer. Update it and run setup again.' }
    return ($values[0].Trim() + ' - driver ' + $values[1].Trim())
}

function New-PluginConfiguration($Existing, [string]$Python, [string]$Ffmpeg, [string]$Ffprobe, [string]$Cache, [bool]$AllDevices) {
    if ($Existing) { $xml = [xml]$Existing.OuterXml }
    else { $xml = [xml]'<?xml version="1.0" encoding="utf-8"?><PluginConfiguration />' }
    $defaults = [ordered]@{ Backend = 'nvidia'; EnableNeuralRendering = 'true'; ContentProfile = 'auto'; EnableAutoHdr = 'false'; EnableFrameInterpolation = 'true';
        VideoMbps = '20'; VsrQuality = 'STREAMING_MEDIUM'; VsrStrength = '0.75';
        NvencPreset = 'p4'; NvencTune = 'default'; NvencLookahead = '0'; NvencSpatialAq = 'false'; NvencTemporalAq = 'false';
        NvencCq = '0'; SegmentType = 'mpegts'; MaxSessions = '1'; SessionMinutes = '180';
        CacheMode = 'memory'; MemoryBudgetMb = '0'; SeekLookaheadSeconds = '90'; SeekCatchupSeconds = '20';
        CacheMaxGb = '10'; CacheMinFreeGb = '5'; CacheIdleMinutes = '10'; EnableRollingCache = 'false'; RollingRewindMinutes = '10';
        ProcessingWidth = '3840'; ProcessingHeight = '2160'; StreamWidth = '3840'; StreamHeight = '2160' }
    foreach ($name in $defaults.Keys) {
        if (-not $xml.DocumentElement.SelectSingleNode($name)) { $node = $xml.CreateElement($name); $node.InnerText = $defaults[$name]; $null = $xml.DocumentElement.AppendChild($node) }
    }
    $updates = [ordered]@{ Enabled = 'true'; EnableAllDevices = $AllDevices.ToString().ToLowerInvariant();
        PythonPath = $Python; FfmpegPath = $Ffmpeg; FfprobePath = $Ffprobe; CacheDirectory = $Cache }
    foreach ($name in $updates.Keys) {
        $node = $xml.DocumentElement.SelectSingleNode($name)
        if (-not $node) { $node = $xml.CreateElement($name); $null = $xml.DocumentElement.AppendChild($node) }
        $node.InnerText = $updates[$name]
    }
    if (-not $xml.DocumentElement.SelectSingleNode('EnabledDeviceIds')) { $null = $xml.DocumentElement.AppendChild($xml.CreateElement('EnabledDeviceIds')) }
    return ,$xml
}

function Write-SetupLog([string]$JobPath, [string]$Text) {
    # PowerShell 5.1 Add-Content denies active readers, including the wizard's log viewer.
    [IO.File]::AppendAllText((Join-Path $JobPath 'setup.log'), ($Text + [Environment]::NewLine), [Text.Encoding]::UTF8)
}

function Write-SetupStatus([string]$JobPath, [string]$Stage, [string]$Message, [int]$Percent) {
    Write-SetupLog $JobPath ((Get-Date -Format 'HH:mm:ss') + '  ' + $Message)
    $status = @{ Stage = $Stage; Message = $Message; Percent = $Percent }
    $temp = Join-Path $JobPath 'status.tmp'
    [IO.File]::WriteAllText($temp, ($status | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    [IO.File]::Copy($temp, (Join-Path $JobPath 'status.json'), $true)
}

function Test-SetupCancellation([string]$JobPath) {
    if (Test-Path -LiteralPath (Join-Path $JobPath 'cancel.request')) { throw [OperationCanceledException]::new('Setup was cancelled before changing Jellyfin.') }
}

function Get-VerifiedDownload($Item, [string]$CachePath, [string]$JobPath) {
    $null = New-Item -ItemType Directory -Force -Path $CachePath
    $file = Join-Path $CachePath ([IO.Path]::GetFileName([string]$Item.file))
    if ((Test-Path -LiteralPath $file) -and (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ieq $Item.sha256) { return $file }
    $uri = [uri]$Item.url
    if ($uri.Scheme -ne 'https') { throw 'Downloads must use HTTPS.' }
    $partial = $file + '.part'
    Add-Type -AssemblyName System.Net.Http
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Test-SetupCancellation $JobPath
        $client = New-Object Net.Http.HttpClient
        $client.Timeout = [TimeSpan]::FromHours(4)
        $response = $null; $input = $null; $output = $null
        try {
            $response = $client.GetAsync($uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            $null = $response.EnsureSuccessStatusCode()
            $input = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
            $output = [IO.File]::Create($partial)
            $buffer = New-Object byte[] (1024 * 1024)
            while (($count = $input.Read($buffer, 0, $buffer.Length)) -gt 0) { Test-SetupCancellation $JobPath; $output.Write($buffer, 0, $count) }
            $output.Dispose(); $output = $null
            if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ine $Item.sha256) { throw ('Download verification failed: ' + $Item.file) }
            [IO.File]::Copy($partial, $file, $true)
            Remove-Item -LiteralPath $partial -Force
            return $file
        } catch [OperationCanceledException] { throw }
        catch { if ($attempt -eq 3) { throw }; Start-Sleep -Seconds $attempt }
        finally { if ($output) { $output.Dispose() }; if ($input) { $input.Dispose() }; if ($response) { $response.Dispose() }; $client.Dispose() }
    }
}

function Test-SetupRuntimeManifest([string]$MarkerPath, [string]$ManifestPath) {
    try {
        $saved = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
        if ($saved.manifestSha256 -eq (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash) { return $true }
        if ($saved.product -ne 'VSR Everywhere' -or $saved.version -notmatch '^\d+\.\d+\.\d+$') { return $false }
        # A product-only version bump does not change the verified runtime dependencies.
        $text = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($ManifestPath))
        $pattern = New-Object Text.RegularExpressions.Regex('(?m)^(\s*"version"\s*:\s*")[^"]+(")')
        $text = $pattern.Replace($text, ('${1}' + $saved.version + '${2}'), 1)
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text))).Replace('-', '') }
        finally { $sha.Dispose() }
        return $hash -eq $saved.manifestSha256
    } catch { return $false }
}

function Invoke-SetupProcess([string]$Executable, [string[]]$Arguments, [string]$JobPath, [int]$TimeoutSeconds = 600, [switch]$NoCancel) {
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $Executable
    $psi.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $temp = Assert-ChildPath $JobPath (Join-Path $JobPath 'temp')
    $null = New-Item -ItemType Directory -Force -Path $temp
    $psi.EnvironmentVariables['TEMP'] = $temp; $psi.EnvironmentVariables['TMP'] = $temp
    $process = [Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    try {
        while (-not $process.WaitForExit(250)) {
            if (-not $NoCancel) { Test-SetupCancellation $JobPath }
            if ([DateTime]::UtcNow -gt $deadline) { throw 'A setup check timed out. See the setup log.' }
        }
        $text = $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()
        if ($text) { Write-SetupLog $JobPath $text }
        return $process.ExitCode
    } finally {
        if (-not $process.HasExited) {
            $kill = Start-Process -FilePath (Join-Path $env:WINDIR 'System32\taskkill.exe') -ArgumentList @('/PID', [string]$process.Id, '/T', '/F') -WindowStyle Hidden -Wait -PassThru
        }
        $process.Dispose()
    }
}

function Expand-SetupArchive([string]$Archive, [string]$Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $null = New-Item -ItemType Directory -Force -Path $Destination
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($entry in $zip.Entries) {
            $path = Assert-ChildPath $Destination (Join-Path $Destination $entry.FullName)
            if ($entry.FullName.EndsWith('/') -or $entry.FullName.EndsWith('\')) { $null = New-Item -ItemType Directory -Force -Path $path; continue }
            $null = New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($path))
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $path, $true)
        }
    } finally { $zip.Dispose() }
}

function Grant-SetupAccess([string]$Path, [string]$Identity, [string]$Rights) {
    if (-not $Identity) { return }
    $account = switch ($Identity) { 'LocalSystem' { '*S-1-5-18' }; 'NT AUTHORITY\NetworkService' { '*S-1-5-20' }; 'NT AUTHORITY\LocalService' { '*S-1-5-19' }; default { if ($Identity -match '^S-1-') { '*' + $Identity } else { $Identity } } }
    & (Join-Path $env:WINDIR 'System32\icacls.exe') $Path '/grant' ($account + ':(OI)(CI)(' + $Rights + ')') '/Q' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not give the Jellyfin account access to its runtime or cache.' }
}

function Stop-SelectedJellyfin($Target) {
    if ($Target.ServiceName) {
        $service = Get-Service -Name $Target.ServiceName
        if ($service.Status -ne 'Stopped') { Stop-Service -Name $Target.ServiceName; $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(45)) }
    } elseif ($Target.ProcessId) {
        $process = Get-Process -Id $Target.ProcessId -ErrorAction SilentlyContinue
        if ($process) {
            if ($process.Path -ine $Target.ServerPath) { throw 'The Jellyfin process changed during setup. Run setup again.' }
            $null = $process.CloseMainWindow()
            if (-not $process.WaitForExit(5000)) { Stop-Process -Id $Target.ProcessId -ErrorAction Stop; $process.WaitForExit(15000) | Out-Null }
        }
    }
    if (Get-Process -Name jellyfin -ErrorAction SilentlyContinue | Where-Object { $_.Path -ieq $Target.ServerPath }) {
        throw 'Jellyfin is still running from this installation. Close it and run setup again.'
    }
}

function Start-SelectedJellyfin($Target) {
    if ($Target.ServiceName) { Start-Service -Name $Target.ServiceName; return }
    # Launch in the signed-in user's normal session, not under the installer's admin token.
    $taskName = 'VsrEverywhere-Start-' + [Guid]::NewGuid().ToString('N')
    try {
        $action = New-ScheduledTaskAction -Execute $Target.ServerPath -Argument $Target.Arguments -WorkingDirectory ([IO.Path]::GetDirectoryName($Target.ServerPath))
        $principal = New-ScheduledTaskPrincipal -UserId $Target.OwnerSid -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
        $null = Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force
        Start-ScheduledTask -TaskName $taskName
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 300
            $started = @(Get-Process -Name jellyfin -ErrorAction SilentlyContinue | Where-Object { $_.Path -ieq $Target.ServerPath })
        } while ($started.Count -eq 0 -and [DateTime]::UtcNow -lt $deadline)
        if ($started.Count -eq 0) { throw 'Jellyfin could not be launched in the signed-in user session.' }
    } finally { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue }
}

function Wait-VsrPlugin([string]$DataPath, [datetime]$Since, [int]$Seconds = 90) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    $pattern = '(?m)^\[(?<timestamp>[^\]]+)\][^\r\n]*Loaded plugin:\s*"?VSR Everywhere"?\s+"?1\.0\.0(?:\.0)?"?\s*$'
    do {
        # An open Jellyfin log can keep its old file timestamp until the server exits.
        foreach ($file in @(Get-ChildItem -LiteralPath (Join-Path $DataPath 'log') -Filter '*.log' -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notlike 'FFmpeg.*' } | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 3)) {
            $text = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction SilentlyContinue
            foreach ($entry in [regex]::Matches([string]$text, $pattern)) {
                $loadedAt = [DateTimeOffset]::MinValue
                if ([DateTimeOffset]::TryParse($entry.Groups['timestamp'].Value, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$loadedAt) -and $loadedAt.UtcDateTime -ge $Since.ToUniversalTime()) { return }
            }
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw ('Jellyfin did not confirm loading VSR Everywhere 1.0.0. Check ' + (Join-Path $DataPath 'log') + ' for startup errors. Setup will restore the previous files.')
}

function Install-VsrPluginFiles([string]$DataPath, [string]$PayloadPath, [string]$BackupPath, $Configuration, [scriptblock]$Verify) {
    $snapshot = Get-PluginSnapshot $DataPath
    $plugins = Join-Path $DataPath 'plugins'
    $destination = Assert-ChildPath $plugins (Join-Path $plugins 'VSR-Everywhere')
    $configPath = Assert-ChildPath $plugins (Join-Path $plugins 'configurations\Jellyfin.Plugin.VsrEverywhere.xml')
    if ((Test-Path -LiteralPath $destination) -and $snapshot.PluginPath -ine $destination) { throw 'The VSR Everywhere destination contains an unrecognized folder. No files were replaced.' }
    $null = New-Item -ItemType Directory -Force -Path $BackupPath
    $oldPlugin = Join-Path $BackupPath 'plugin'
    $oldConfig = Join-Path $BackupPath 'configuration.xml'
    $hadConfig = Test-Path -LiteralPath $snapshot.ConfigPath -PathType Leaf
    if ($hadConfig) { Copy-Item -LiteralPath $snapshot.ConfigPath -Destination $oldConfig }
    $record = @{ Product = 'VSR Everywhere'; Version = '1.0.0'; DataPath = $DataPath; PluginPath = $snapshot.PluginPath;
        OriginalConfigPath = $snapshot.ConfigPath; ConfigPath = $configPath; HadConfig = $hadConfig }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $BackupPath 'restore.json') -Encoding UTF8
    $pluginMoved = $false; $newPlugin = $false; $configWritten = $false
    try {
        if ($snapshot.PluginPath) {
            $null = Assert-ChildPath $plugins $snapshot.PluginPath
            Move-Item -LiteralPath $snapshot.PluginPath -Destination $oldPlugin
            $pluginMoved = $true
        }
        $null = New-Item -ItemType Directory -Force -Path $destination
        $newPlugin = $true
        Get-ChildItem -LiteralPath $PayloadPath -Force | Copy-Item -Destination $destination -Recurse -Force
        $null = New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($configPath))
        $Configuration.Save($configPath)
        $configWritten = $true
        if ($hadConfig -and $snapshot.ConfigPath -ine $configPath) { Remove-Item -LiteralPath $snapshot.ConfigPath -Force }
        & $Verify
        return [pscustomobject]@{ PluginPath = $destination; BackupPath = $BackupPath }
    } catch {
        $failure = $_
        if ($newPlugin -and (Test-Path -LiteralPath $destination)) {
            $null = Assert-ChildPath $plugins $destination
            Move-Item -LiteralPath $destination -Destination (Join-Path $BackupPath 'failed-install')
        }
        if ($pluginMoved) { Move-Item -LiteralPath $oldPlugin -Destination $snapshot.PluginPath }
        if ($configWritten -and (Test-Path -LiteralPath $configPath)) { Remove-Item -LiteralPath $configPath -Force }
        if ($hadConfig) { Copy-Item -LiteralPath $oldConfig -Destination $snapshot.ConfigPath -Force }
        throw $failure
    }
}


function Install-VsrCppRuntime($Manifest, [string]$Downloads, [string]$JobPath) {
    $vc = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
    if (-not $vc -or -not $vc.Installed -or [version]([string]$vc.Version).TrimStart('v') -lt [version]'14.50.0.0') {
        $vcPath = Join-Path $downloads 'vc_redist.x64.exe'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $manifest.visualCppUrl -OutFile $vcPath -UseBasicParsing
        $signature = Get-AuthenticodeSignature -LiteralPath $vcPath
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation(?:,|$)') { throw 'The Microsoft runtime download did not have a valid Microsoft signature.' }
        $code = Invoke-SetupProcess $vcPath @('/install', '/quiet', '/norestart') $JobPath 600
        if ($code -eq 3010) { throw 'The Microsoft runtime needs a Windows restart. Restart Windows, then run setup again. Jellyfin has not been changed.' }
        if ($code -notin @(0, 1638)) { throw 'The Microsoft runtime installer could not finish. See the setup log.' }
    }
}

function Test-SetupAdministrator {
    $identity = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Export-ModuleMember -Function *
