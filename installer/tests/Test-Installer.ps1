$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -Global
$installer = Split-Path $PSScriptRoot -Parent
Import-Module (Join-Path $installer 'Installer.Core.psm1') -Force -DisableNameChecking
$work = Join-Path $PSScriptRoot ('work\' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Force -Path $work
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
}
function Expect-Failure([scriptblock]$Action, [string]$Message) {
    $failed = $false
    try { & $Action | Out-Null } catch { $failed = $true }
    Check $failed $Message
}
function New-Fixture([string]$Name) {
    $data = Join-Path $work $Name
    $plugin = Join-Path $data 'plugins\SelectedPlugin'
    $config = Join-Path $data 'plugins\configurations\Jellyfin.Plugin.Fixture.xml'
    $null = New-Item -ItemType Directory -Force -Path $plugin, ([IO.Path]::GetDirectoryName($config)), (Join-Path $data 'config')
    @{ guid = '7ca129d0-7454-41c7-aad6-55250b1a7b5b'; name = 'VSR Everywhere'; version = '1.0.0.0' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $plugin 'meta.json') -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $plugin 'Jellyfin.Plugin.Fixture.dll') -Value 'fixture plugin'
    Set-Content -LiteralPath (Join-Path $data 'config\system.xml') -Value '<ServerConfiguration />'
    Set-Content -LiteralPath $config -Value '<PluginConfiguration><Enabled>false</Enabled><EnabledDeviceIds><string>living-room</string></EnabledDeviceIds><ProcessingWidth>5120</ProcessingWidth><ProcessingHeight>2880</ProcessingHeight><StreamWidth>3840</StreamWidth><StreamHeight>2160</StreamHeight><VsrQuality>ULTRA</VsrQuality><UnknownSetting>keep</UnknownSetting></PluginConfiguration>' -Encoding UTF8
    [pscustomobject]@{ Data = $data; Plugin = $plugin; Config = $config; ConfigHash = (Get-FileHash -LiteralPath $config).Hash }
}
$fixture = New-Fixture 'configuration'
$snapshot = Get-PluginSnapshot $fixture.Data
Check ($snapshot.PluginPath -eq $fixture.Plugin) 'Plugin discovery by stable ID failed.'
Check ($snapshot.ConfigPath -eq $fixture.Config) 'Configuration discovery from installed assembly failed.'
$configuration = New-PluginConfiguration $snapshot.Config 'C:\Runtime\python.exe' 'C:\Encoder\ffmpeg.exe' 'C:\Encoder\ffprobe.exe' 'D:\Cache' $false
Check ((Get-XmlValue $configuration 'ProcessingWidth') -eq '5120') '5K processing was not retained.'
Check ((Get-XmlValue $configuration 'StreamWidth') -eq '3840') '4K stream was not retained.'
Check ((Get-XmlValue $configuration 'VsrQuality') -eq 'ULTRA') 'Quality was not retained.'
Check ((Get-XmlValue $configuration 'UnknownSetting') -eq 'keep') 'Unknown settings were lost.'
Check ($configuration.DocumentElement.SelectSingleNode('EnabledDeviceIds/string').InnerText -eq 'living-room') 'Device selection was not retained.'
Check ((Get-XmlValue $configuration 'Enabled') -eq 'true') 'Plugin was not enabled.'
Check ((Get-XmlValue $configuration 'EnableAllDevices') -eq 'false') 'Specific device mode was changed.'
Check ((Get-XmlValue $configuration 'PythonPath') -eq 'C:\Runtime\python.exe') 'Runtime path was not configured.'
Check ((Get-FileHash -LiteralPath $fixture.Config).Hash -eq $fixture.ConfigHash) 'Preparation changed the original configuration.'
$fresh = New-PluginConfiguration $null 'python' 'ffmpeg' 'ffprobe' 'cache' $true
Check ((Get-XmlValue $fresh 'EnableAllDevices') -eq 'true') 'New setup must enable all devices by default.'
Check ((Get-XmlValue $fresh 'ProcessingWidth') -eq '3840' -and (Get-XmlValue $fresh 'StreamWidth') -eq '3840') 'New setup defaults must be 4K.'
Check ((Get-XmlValue $fresh 'SegmentType') -eq 'mpegts') 'New setup should use MPEG-TS/AAC.'

$payload = Join-Path $work 'payload'
$null = New-Item -ItemType Directory -Force -Path (Join-Path $payload 'worker')
Set-Content -LiteralPath (Join-Path $payload 'Jellyfin.Plugin.VsrEverywhere.dll') -Value 'new plugin'
Set-Content -LiteralPath (Join-Path $payload 'worker\vsr_everywhere.py') -Value '# fixture'
@{ guid = '7ca129d0-7454-41c7-aad6-55250b1a7b5b'; name = 'VSR Everywhere'; version = '1.0.0.0' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $payload 'meta.json') -Encoding UTF8
$success = New-Fixture 'success'
$backup = Join-Path $success.Data 'vsr-everywhere-backups\check'
$installed = Install-VsrPluginFiles $success.Data $payload $backup $configuration { }
Check (Test-Path -LiteralPath (Join-Path $installed.PluginPath 'Jellyfin.Plugin.VsrEverywhere.dll')) 'New plugin DLL missing.'
Check (-not (Test-Path -LiteralPath $success.Plugin)) 'A duplicate plugin folder was left behind.'
Check (-not (Test-Path -LiteralPath $success.Config)) 'A duplicate configuration was left behind.'
Check (Test-Path -LiteralPath (Join-Path $success.Data 'plugins\configurations\Jellyfin.Plugin.VsrEverywhere.xml')) 'Branded configuration missing.'
Check ((Get-FileHash -LiteralPath (Join-Path $backup 'configuration.xml')).Hash -eq $success.ConfigHash) 'Configuration backup differs from original.'
Check (Test-Path -LiteralPath (Join-Path $backup 'plugin\Jellyfin.Plugin.Fixture.dll')) 'Plugin backup missing.'

$rollback = New-Fixture 'rollback'
$rollbackBackup = Join-Path $rollback.Data 'vsr-everywhere-backups\check'
Expect-Failure { Install-VsrPluginFiles $rollback.Data $payload $rollbackBackup $configuration { throw 'Simulated startup failure' } } 'Startup failure did not fail the install.'
Check (Test-Path -LiteralPath (Join-Path $rollback.Plugin 'Jellyfin.Plugin.Fixture.dll')) 'Rollback did not restore the plugin.'
Check ((Get-FileHash -LiteralPath $rollback.Config).Hash -eq $rollback.ConfigHash) 'Rollback did not restore the exact configuration.'
Check (-not (Test-Path -LiteralPath (Join-Path $rollback.Data 'plugins\VSR-Everywhere'))) 'Failed plugin remained in the scan directory.'
Check (-not (Test-Path -LiteralPath (Join-Path $rollback.Data 'plugins\configurations\Jellyfin.Plugin.VsrEverywhere.xml'))) 'Failed configuration remained active.'

$unknown = New-Fixture 'unknown-destination'
$null = New-Item -ItemType Directory -Force -Path (Join-Path $unknown.Data 'plugins\VSR-Everywhere')
Set-Content -LiteralPath (Join-Path $unknown.Data 'plugins\VSR-Everywhere\unrelated.txt') -Value 'preserve'
Expect-Failure { Install-VsrPluginFiles $unknown.Data $payload (Join-Path $unknown.Data 'backup') $configuration { } } 'Unrecognized destination was overwritten.'
Check (Test-Path -LiteralPath (Join-Path $unknown.Data 'plugins\VSR-Everywhere\unrelated.txt')) 'An unrelated file was changed.'
Expect-Failure { Get-PluginSnapshot $unknown.Data } 'Unknown destination was not rejected during preflight.'

$manualAssembly = Join-Path $work 'Jellyfin.Plugin.VsrEverywhere.dll'
Add-Type -TypeDefinition 'public sealed class VsrManualInstallFixture { }' -OutputAssembly $manualAssembly
$foreignAssembly = Join-Path $work 'ForeignPlugin.dll'
Add-Type -TypeDefinition 'public sealed class ForeignInstallFixture { }' -OutputAssembly $foreignAssembly
function New-ManualFixture([string]$Name) {
    $original = New-Fixture $Name
    $source = Assert-ChildPath $original.Data $original.Plugin
    $destination = Assert-ChildPath $original.Data (Join-Path $original.Data 'plugins\VSR-Everywhere')
    Move-Item -LiteralPath $source -Destination $destination
    Remove-Item -LiteralPath (Join-Path $destination 'meta.json'), (Join-Path $destination 'Jellyfin.Plugin.Fixture.dll')
    Copy-Item -LiteralPath $manualAssembly -Destination (Join-Path $destination 'Jellyfin.Plugin.VsrEverywhere.dll')
    $configPath = Join-Path $original.Data 'plugins\configurations\Jellyfin.Plugin.VsrEverywhere.xml'
    Move-Item -LiteralPath $original.Config -Destination $configPath
    return [pscustomobject]@{ Data = $original.Data; Plugin = $destination; Config = $configPath; ConfigHash = $original.ConfigHash }
}
$manual = New-ManualFixture 'manual-success'
$manualDll = Join-Path $manual.Plugin 'Jellyfin.Plugin.VsrEverywhere.dll'
$manualHash = (Get-FileHash -LiteralPath $manualDll).Hash
$manualSnapshot = Get-PluginSnapshot $manual.Data
Check ($manualSnapshot.PluginPath -eq $manual.Plugin) 'DLL-only installation was not recognized.'
Check ($manualSnapshot.ConfigPath -eq $manual.Config) 'Manual installation configuration was not found.'
$manualConfig = New-PluginConfiguration $manualSnapshot.Config 'C:\Runtime\python.exe' 'C:\Encoder\ffmpeg.exe' 'C:\Encoder\ffprobe.exe' 'D:\Cache' $false
$manualBackup = Join-Path $manual.Data 'vsr-everywhere-backups\check'
$null = Install-VsrPluginFiles $manual.Data $payload $manualBackup $manualConfig { }
Check ((Get-FileHash -LiteralPath (Join-Path $manualBackup 'plugin\Jellyfin.Plugin.VsrEverywhere.dll')).Hash -eq $manualHash) 'Manual DLL backup differs from original.'
Check ((Get-FileHash -LiteralPath (Join-Path $manualBackup 'configuration.xml')).Hash -eq $manual.ConfigHash) 'Manual configuration backup differs from original.'
Check (Test-Path -LiteralPath (Join-Path $manual.Plugin 'meta.json')) 'Manual upgrade did not install plugin metadata.'
Check (Test-Path -LiteralPath (Join-Path $manual.Plugin 'worker\vsr_everywhere.py')) 'Manual upgrade did not install the worker.'
$manualSaved = [xml](Get-Content -LiteralPath $manual.Config -Raw)
Check ((Get-XmlValue $manualSaved 'ProcessingWidth') -eq '5120' -and (Get-XmlValue $manualSaved 'StreamWidth') -eq '3840') 'Manual upgrade changed resolutions.'
Check ($manualSaved.DocumentElement.SelectSingleNode('EnabledDeviceIds/string').InnerText -eq 'living-room') 'Manual upgrade lost device approvals.'
Check ((Get-XmlValue $manualSaved 'UnknownSetting') -eq 'keep') 'Manual upgrade lost unknown settings.'

$manualRollback = New-ManualFixture 'manual-rollback'
$rollbackDll = Join-Path $manualRollback.Plugin 'Jellyfin.Plugin.VsrEverywhere.dll'
$rollbackHash = (Get-FileHash -LiteralPath $rollbackDll).Hash
Expect-Failure { Install-VsrPluginFiles $manualRollback.Data $payload (Join-Path $manualRollback.Data 'backup') $manualConfig { throw 'Simulated startup failure' } } 'Manual rollback did not report verification failure.'
Check ((Get-FileHash -LiteralPath $rollbackDll).Hash -eq $rollbackHash) 'Manual rollback did not restore the exact DLL.'
Check ((Get-FileHash -LiteralPath $manualRollback.Config).Hash -eq $manualRollback.ConfigHash) 'Manual rollback did not restore the exact configuration.'
Check (-not (Test-Path -LiteralPath (Join-Path $manualRollback.Plugin 'meta.json'))) 'Manual rollback added metadata to the original installation.'
Check ((Get-PluginSnapshot $manualRollback.Data).PluginPath -eq $manualRollback.Plugin) 'Restored manual installation was not recognized.'

$wrongIdentity = New-ManualFixture 'manual-foreign-identity'
$wrongDll = Join-Path $wrongIdentity.Plugin 'Jellyfin.Plugin.VsrEverywhere.dll'
Copy-Item -LiteralPath $foreignAssembly -Destination $wrongDll -Force
$foreignHash = (Get-FileHash -LiteralPath $wrongDll).Hash
Expect-Failure { Get-PluginSnapshot $wrongIdentity.Data } 'A renamed foreign assembly was accepted.'
Check ((Get-FileHash -LiteralPath $wrongDll).Hash -eq $foreignHash) 'Foreign assembly was modified during discovery.'
$invalidIdentity = New-ManualFixture 'manual-invalid-assembly'
Set-Content -LiteralPath (Join-Path $invalidIdentity.Plugin 'Jellyfin.Plugin.VsrEverywhere.dll') -Value 'not an assembly'
Expect-Failure { Get-PluginSnapshot $invalidIdentity.Data } 'A non-assembly DLL was accepted.'
$conflictingMeta = New-ManualFixture 'manual-conflicting-metadata'
@{ guid = '00000000-0000-0000-0000-000000000000' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $conflictingMeta.Plugin 'meta.json') -Encoding UTF8
Expect-Failure { Get-PluginSnapshot $conflictingMeta.Data } 'Conflicting metadata was bypassed using the DLL identity.'

$safe = Join-Path $work 'safe'
$null = New-Item -ItemType Directory -Force -Path $safe
Check ((Get-SetupStorageRoot $safe) -eq (Join-Path $safe 'VsrEverywhere')) 'Selected storage folder was not used.'
Check ((Get-SetupStorageRoot '') -eq (Join-Path $env:ProgramData 'VsrEverywhere')) 'Default storage location changed.'
Expect-Failure { Get-SetupStorageRoot 'relative\folder' } 'A relative runtime folder was accepted.'
Expect-Failure { Get-SetupStorageRoot '\\server\share' } 'A network runtime folder was accepted.'
Expect-Failure { Assert-ChildPath $safe (Join-Path $safe '..\escape') } 'Path traversal was accepted.'
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression
$zipPath = Join-Path $work 'invalid.zip'
$zip = [IO.Compression.ZipFile]::Open($zipPath, [IO.Compression.ZipArchiveMode]::Create)
$entry = $zip.CreateEntry('../escape.txt'); $writer = New-Object IO.StreamWriter($entry.Open()); $writer.Write('no'); $writer.Dispose(); $zip.Dispose()
Expect-Failure { Expand-SetupArchive $zipPath $safe } 'Archive path traversal was accepted.'
Check (-not (Test-Path -LiteralPath (Join-Path $work 'escape.txt'))) 'Archive wrote outside its destination.'

$job = Join-Path $work 'job'
$cache = Join-Path $work 'cache'
$null = New-Item -ItemType Directory -Force -Path $job, $cache
$cached = Join-Path $cache 'component.whl'; Set-Content -LiteralPath $cached -Value 'verified payload'
$item = [pscustomobject]@{ file = 'component.whl'; url = 'https://invalid.example/component.whl'; sha256 = (Get-FileHash -LiteralPath $cached).Hash }
Check ((Get-VerifiedDownload $item $cache $job) -eq $cached) 'A verified cached component was not reused.'
$item.sha256 = '0' * 64; $item.url = 'http://invalid.example/component.whl'
Expect-Failure { Get-VerifiedDownload $item $cache $job } 'Corrupt cached download or non-HTTPS URL was accepted.'
Set-Content -LiteralPath (Join-Path $job 'cancel.request') -Value ''
Expect-Failure { Test-SetupCancellation $job } 'Cancellation was ignored.'
Remove-Item -LiteralPath (Join-Path $job 'cancel.request') -Force

Write-SetupStatus $job 'download' 'Starting log sharing check' 1
$log = Join-Path $job 'setup.log'
$reader = [IO.File]::Open($log, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
try {
    Write-SetupStatus $job 'download' 'Writer survived an active log reader' 2
} finally { $reader.Dispose() }
Check ((Get-Content -LiteralPath $log -Raw).Contains('Writer survived an active log reader')) 'Progress logging failed while the wizard was reading.'
$unicode = 'Unicode log: ' + [char]0x03b1
Write-SetupLog $job $unicode
Check ((Get-Content -LiteralPath $log -Raw).Contains($unicode)) 'Log text lost its UTF-8 encoding.'
$writer = [IO.File]::Open($log, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::Read)
try {
    Check ((Get-Content -LiteralPath $log -Raw).Contains($unicode)) 'Wizard could not read while the log writer was active.'
} finally { $writer.Dispose() }

$verificationData = Join-Path $work 'startup-verification'
$verificationLog = Join-Path $verificationData 'log\log_today.log'
$null = New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($verificationLog))
$since = [DateTime]::UtcNow.AddMinutes(-1)
$freshLine = '[' + [DateTimeOffset]::Now.ToString('yyyy-MM-dd HH:mm:ss.fff zzz', [Globalization.CultureInfo]::InvariantCulture) + '] [INF] PluginManager: Loaded plugin: "VSR Everywhere" "1.0.0.0"'
Set-Content -LiteralPath $verificationLog -Value $freshLine -Encoding UTF8
[IO.File]::SetLastWriteTimeUtc($verificationLog, $since.AddDays(-1))
foreach ($index in 1..4) { Set-Content -LiteralPath (Join-Path $verificationData ('log\FFmpeg.Transcode-' + $index + '.log')) -Value 'Encoder output' }
Wait-VsrPlugin $verificationData $since 0
Check $true 'A fresh plugin confirmation was missed because the open log had an old file timestamp.'
$staleLine = '[' + [DateTimeOffset]::Now.AddDays(-1).ToString('yyyy-MM-dd HH:mm:ss.fff zzz', [Globalization.CultureInfo]::InvariantCulture) + '] [INF] PluginManager: Loaded plugin: "VSR Everywhere" "1.0.0.0"'
Set-Content -LiteralPath $verificationLog -Value $staleLine -Encoding UTF8
Expect-Failure { Wait-VsrPlugin $verificationData $since 0 } 'A previous startup was accepted as the new plugin confirmation.'
Set-Content -LiteralPath $verificationLog -Value $freshLine.Replace('1.0.0.0', '1.0.0.1') -Encoding UTF8
Expect-Failure { Wait-VsrPlugin $verificationData $since 0 } 'A different plugin version was accepted.'

$python = (Get-Command python.exe).Source
$argumentProbe = Join-Path $work 'arguments.py'
$argumentReport = Join-Path $work 'arguments.json'
Set-Content -LiteralPath $argumentProbe -Value 'import json,sys;open(sys.argv[1],"w").write(json.dumps(sys.argv[2:]))' -Encoding UTF8
$values = @('plain', 'C:\Path with spaces\', 'embedded"quote', '', 'C:\last\')
$code = Invoke-SetupProcess $python (@($argumentProbe, $argumentReport) + $values) $job
Check ($code -eq 0) 'Native argument test did not run.'
$captured = Get-Content -LiteralPath $argumentReport -Raw | ConvertFrom-Json
Check ($captured.Count -eq $values.Count) 'Native argument count changed.'
for ($i = 0; $i -lt $values.Count; $i++) { Check ($captured[$i] -ceq $values[$i]) ('Native argument was changed at position ' + $i) }
$tempProbe = Join-Path $work 'temp.py'
Set-Content -LiteralPath $tempProbe -Value 'import json,os,sys,tempfile;open(sys.argv[1],"w").write(json.dumps([os.environ["TEMP"],os.environ["TMP"],tempfile.gettempdir()]))' -Encoding UTF8
$code = Invoke-SetupProcess $python @($tempProbe, $argumentReport) $job
$captured = Get-Content -LiteralPath $argumentReport -Raw | ConvertFrom-Json
Check ($code -eq 0 -and (@($captured | Where-Object { $_ -ine (Join-Path $job 'temp') }).Count -eq 0)) 'Child processes used a temporary folder outside the setup job.'

foreach ($file in @(Get-ChildItem -LiteralPath $installer -File | Where-Object { $_.Extension -in @('.ps1', '.psm1') })) {
    $errors = $null; $tokens = $null
    $null = [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    Check ($errors.Count -eq 0) ('PowerShell syntax error in ' + $file.Name)
}
$runtimeManifest = Join-Path $work 'runtime-manifest.json'
$previousManifest = Join-Path $work 'previous-runtime-manifest.json'
$runtimeMarker = Join-Path $work 'runtime-marker.json'
$manifestJson = @{ product = 'VSR Everywhere'; version = '1.0.0'; dependencySha256 = ('a' * 64) } | ConvertTo-Json
[IO.File]::WriteAllText($runtimeManifest, $manifestJson, (New-Object Text.UTF8Encoding($false)))
[IO.File]::WriteAllText($previousManifest, $manifestJson.Replace('"1.0.0"', '"0.0.1"'), (New-Object Text.UTF8Encoding($false)))
@{ product = 'VSR Everywhere'; version = '0.0.1'; manifestSha256 = (Get-FileHash -LiteralPath $previousManifest).Hash } | ConvertTo-Json | Set-Content -LiteralPath $runtimeMarker -Encoding UTF8
Check (Test-SetupRuntimeManifest $runtimeMarker $runtimeManifest) 'A version-only release could not reuse its verified runtime.'
[IO.File]::WriteAllText($runtimeManifest, $manifestJson.Replace(('a' * 64), ('b' * 64)), (New-Object Text.UTF8Encoding($false)))
Check (-not (Test-SetupRuntimeManifest $runtimeMarker $runtimeManifest)) 'A runtime with different dependencies was accepted.'
@{ product = 'VSR Everywhere'; version = '1.0.0'; manifestSha256 = (Get-FileHash -LiteralPath $runtimeManifest).Hash } | ConvertTo-Json | Set-Content -LiteralPath $runtimeMarker -Encoding UTF8
Check (Test-SetupRuntimeManifest $runtimeMarker $runtimeManifest) 'An exact runtime manifest was not reused.'

function Test-Engine([string]$Name, [bool]$FailStartup, [bool]$CustomStorage = $false, [bool]$PreviousRuntime = $false, [bool]$UnknownDestination = $false) {
    $fixture = New-Fixture ($Name + '\data')
    if ($UnknownDestination) {
        $unknownPath = Join-Path $fixture.Data 'plugins\VSR-Everywhere'
        $null = New-Item -ItemType Directory -Force -Path $unknownPath
        Set-Content -LiteralPath (Join-Path $unknownPath 'unrelated.txt') -Value 'preserve'
    }
    $case = Join-Path $work $Name
    $engine = Join-Path $case 'engine'
    $programData = Join-Path $case 'ProgramData'
    $job = Join-Path $case 'job'
    $null = New-Item -ItemType Directory -Force -Path $engine, $programData, $job
    Copy-Item -LiteralPath (Join-Path $installer 'Install-Engine.ps1') -Destination $engine
    Copy-Item -LiteralPath $payload -Destination (Join-Path $engine 'plugin') -Recurse
    $encoderZip = Join-Path $case 'encoder.zip'
    $encoderSource = Join-Path $case 'encoder-source'
    $null = New-Item -ItemType Directory -Force -Path $encoderSource
    Set-Content -LiteralPath (Join-Path $encoderSource 'ffmpeg.exe') -Value 'fixture'
    Set-Content -LiteralPath (Join-Path $encoderSource 'ffprobe.exe') -Value 'fixture'
    [IO.Compression.ZipFile]::CreateFromDirectory($encoderSource, $encoderZip)
    $manifest = Get-Content -LiteralPath (Join-Path $installer 'downloads.lock.json') -Raw | ConvertFrom-Json
    $manifest.ffmpeg.file = 'encoder.zip'
    $manifest.ffmpeg.sha256 = (Get-FileHash -LiteralPath $encoderZip).Hash.ToLowerInvariant()
    $manifest.ffmpeg.url = 'https://invalid.example/encoder.zip'
    $lockPath = Join-Path $engine 'downloads.lock.json'
    $manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $lockPath -Encoding UTF8
    $lockHash = (Get-FileHash -LiteralPath $lockPath).Hash.ToLowerInvariant()
    $storageFolder = if ($CustomStorage) { Join-Path $case 'Custom storage' } else { $programData }
    $storageRoot = Join-Path $storageFolder 'VsrEverywhere'
    $runtimeVersion = '1.0.0'; $savedHash = $lockHash
    if ($PreviousRuntime) {
        $runtimeVersion = '0.0.1'
        $priorLock = Join-Path $case 'prior-lock.json'
        $priorText = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($lockPath))
        $priorText = [regex]::Replace($priorText, '(?m)^(\s*"version"\s*:\s*")1\.0\.0(")', '${1}0.0.1${2}')
        [IO.File]::WriteAllBytes($priorLock, [Text.Encoding]::UTF8.GetBytes($priorText))
        $savedHash = (Get-FileHash -LiteralPath $priorLock).Hash.ToLowerInvariant()
    }
    $runtime = Join-Path $storageRoot ('runtimes\' + $runtimeVersion + '-' + $savedHash.Substring(0, 12))
    $cache = Join-Path $storageRoot 'downloads'
    $null = New-Item -ItemType Directory -Force -Path $runtime, $cache
    @{ product = 'VSR Everywhere'; version = $runtimeVersion; manifestSha256 = $savedHash } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime 'vsr-runtime.json') -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $runtime 'python.exe') -Value 'fixture'
    Copy-Item -LiteralPath $encoderZip -Destination (Join-Path $cache 'encoder.zip')
    $hashes = @{}
    foreach ($file in Get-ChildItem -LiteralPath (Join-Path $engine 'plugin') -Recurse -File) {
        $relative = $file.FullName.Substring((Join-Path $engine 'plugin').Length + 1).Replace('\', '/')
        $hashes[$relative] = (Get-FileHash -LiteralPath $file.FullName).Hash
    }
    $hashes | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $engine 'payload.sha256.json') -Encoding UTF8
    $mocks = @'

function Test-SetupAdministrator { return $true }
function Get-JellyfinTarget([string]$DataPath, [string]$ServerPath) {
    if ($DataPath -ne $env:VSR_TEST_DATA) { throw 'Test attempted to leave its isolated data folder.' }
    return [pscustomobject]@{ DataPath = $DataPath; ServerPath = $ServerPath; Version = '12.1.0'; ServiceName = ''; ServiceIdentity = ''; OwnerSid = 'fixture'; WasRunning = $true; Arguments = ''; Url = 'http://localhost:8096'; ProcessId = 0 }
}
function Get-NvidiaStatus { return 'fixture GPU' }
function Install-VsrCppRuntime { }
function Set-Acl { }
function Grant-SetupAccess { }
function Get-VerifiedDownload {
    if ($args[0].file -like 'Veyra-*') { return 'verified fixture archive' }
    return Join-Path $args[1] $args[0].file
}
function Invoke-SetupProcess {
    if ($args[1][0] -like '*prepare_veyra.py') { return 0 }
    Add-Content -LiteralPath $env:VSR_TEST_EVENTS -Value 'runtime-test'
    return 0
}
function Stop-SelectedJellyfin { Add-Content -LiteralPath $env:VSR_TEST_EVENTS -Value 'stop' }
function Start-SelectedJellyfin { Add-Content -LiteralPath $env:VSR_TEST_EVENTS -Value 'start' }
function Wait-VsrPlugin {
    Add-Content -LiteralPath $env:VSR_TEST_EVENTS -Value 'verify'
    if ($env:VSR_TEST_FAIL -eq 'yes') { throw 'Simulated startup failure' }
}
Export-ModuleMember -Function *
'@
    $module = (Get-Content -LiteralPath (Join-Path $installer 'Installer.Core.psm1') -Raw) + $mocks
    Set-Content -LiteralPath (Join-Path $engine 'Installer.Core.psm1') -Value $module -Encoding UTF8
    $plan = @{ Product = 'VSR Everywhere'; Version = '1.0.0'; DataPath = $fixture.Data; ServerPath = (Join-Path $case 'jellyfin.exe'); AllDevices = $false }
    if ($CustomStorage) { $plan.InstallFolder = $storageFolder }
    $plan | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $job 'plan.json') -Encoding UTF8
    $events = Join-Path $case 'events.txt'
    $wrapper = Join-Path $case 'run.ps1'
    $quote = { param($text) "'" + $text.Replace("'", "''") + "'" }
    $lines = @(
        ('$env:ProgramData = ' + (& $quote $programData)),
        ('$env:VSR_TEST_DATA = ' + (& $quote $fixture.Data)),
        ('$env:VSR_TEST_EVENTS = ' + (& $quote $events)),
        ('$env:VSR_TEST_FAIL = ' + (& $quote $(if ($FailStartup) { 'yes' } else { 'no' }))),
        ('& ' + (& $quote (Join-Path $engine 'Install-Engine.ps1')) + ' -JobPath ' + (& $quote $job)),
        'exit $LASTEXITCODE'
    )
    $lines | Set-Content -LiteralPath $wrapper -Encoding UTF8
    $code = Invoke-SetupProcess (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $wrapper) $job 60
    $result = Get-Content -LiteralPath (Join-Path $job 'result.json') -Raw | ConvertFrom-Json
    if ($UnknownDestination) {
        Check ($code -eq 1 -and -not $result.Success -and $result.Message -like '*unrecognized folder*') 'Engine did not reject an unknown destination.'
        Check (-not (Test-Path -LiteralPath $events)) 'Engine tested the runtime or stopped Jellyfin before rejecting the destination.'
        Check ((Get-FileHash -LiteralPath $fixture.Config).Hash -eq $fixture.ConfigHash) 'Preflight changed the original configuration.'
        Check ((Get-Content -LiteralPath (Join-Path $unknownPath 'unrelated.txt') -Raw).Trim() -eq 'preserve') 'Preflight changed an unrelated file.'
        return
    }
    if ($FailStartup) {
        Check ($code -eq 1 -and -not $result.Success) 'Engine did not report startup failure.'
        Check ((Get-FileHash -LiteralPath $fixture.Config).Hash -eq $fixture.ConfigHash) 'Engine rollback did not restore exact settings.'
        Check (Test-Path -LiteralPath (Join-Path $fixture.Plugin 'Jellyfin.Plugin.Fixture.dll')) 'Engine rollback did not restore the plugin.'
        $sequence = @(Get-Content -LiteralPath $events)
        Check (($sequence -join ',') -eq 'runtime-test,stop,start,verify,stop,start') 'Rollback did not stop the failed server before restoring files and restarting.'
    } else {
        Check ($code -eq 0 -and $result.Success) ('Engine install failed: ' + $result.Message)
        Check (Test-Path -LiteralPath (Join-Path $fixture.Data 'plugins\VSR-Everywhere\Jellyfin.Plugin.VsrEverywhere.dll')) 'Engine did not deploy the plugin.'
        Check ((@(Get-Content -LiteralPath $events) -join ',') -eq 'runtime-test,stop,start,verify') 'Engine changed Jellyfin before checking the runtime.'
        $saved = [xml](Get-Content -LiteralPath (Join-Path $fixture.Data 'plugins\configurations\Jellyfin.Plugin.VsrEverywhere.xml') -Raw)
        Check ((Get-XmlValue $saved 'PythonPath') -eq (Join-Path $runtime 'python.exe')) 'Engine configured a runtime outside the selected folder.'
        Check (Test-Path -LiteralPath (Join-Path $storageRoot ('encoders\' + $manifest.ffmpeg.sha256.Substring(0, 12) + '\ffmpeg.exe'))) 'Encoder was not installed under the selected folder.'
        if ($CustomStorage) { Check (-not (Test-Path -LiteralPath (Join-Path $programData 'VsrEverywhere'))) 'Custom storage created runtime files in ProgramData.' }
    }
}
Test-Engine 'engine-success' $false
Test-Engine 'engine-rollback' $true
Test-Engine 'engine-custom-storage' $false $true
Test-Engine 'engine-version-upgrade' $false $true $true
Test-Engine 'engine-unknown-destination' $false $false $false $true
Write-Output "PASS: $script:checks installer checks (settings, discovery, deployment, rollback, paths, downloads, cancellation, native arguments, and complete engine transactions)."
