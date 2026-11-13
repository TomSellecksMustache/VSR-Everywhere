$ErrorActionPreference = 'Stop'
$installer = Split-Path $PSScriptRoot -Parent
Import-Module (Join-Path $installer 'Installer.Core.psm1') -Force
$manifest = Get-Content -LiteralPath (Join-Path $installer 'downloads.lock.json') -Raw | ConvertFrom-Json
$job = Join-Path $installer 'build\online-test'
$null = New-Item -ItemType Directory -Force -Path $job
Get-NvidiaStatus | Write-Output
$archive = Get-VerifiedDownload $manifest.ffmpeg (Join-Path $installer 'build\downloads') $job
$encoder = Join-Path $installer 'build\test-encoder'
Expand-SetupArchive $archive $encoder
$ffmpeg = @(Get-ChildItem -LiteralPath $encoder -Filter ffmpeg.exe -File -Recurse)[0].FullName
$ffprobe = @(Get-ChildItem -LiteralPath $encoder -Filter ffprobe.exe -File -Recurse)[0].FullName
$python = Join-Path $installer 'build\test-runtime\python.exe'
$arguments = @((Join-Path $installer 'Test-Runtime.py'), '--worker', (Join-Path (Split-Path $installer -Parent) 'worker\vsr_everywhere.py'),
    '--ffmpeg', $ffmpeg, '--ffprobe', $ffprobe, '--report', (Join-Path $job 'runtime-test.json'))
$code = Invoke-SetupProcess $python $arguments $job 420
Get-Content -LiteralPath (Join-Path $job 'setup.log') -Tail 80
if ($code -ne 0) { throw 'The clean private runtime did not pass the GPU/encoding test.' }
Get-Content -LiteralPath (Join-Path $job 'runtime-test.json') -Raw
