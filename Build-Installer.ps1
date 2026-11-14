[CmdletBinding()]
param([switch]$SkipPluginBuild, [string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -Global
$root = $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root 'dist' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $root 'dist')).TrimEnd('\')
if ($OutputDirectory -ne $allowedRoot -and -not $OutputDirectory.StartsWith($allowedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Build output must stay inside this project dist folder.' }
if (-not $SkipPluginBuild) { & (Join-Path $root 'Build-Windows.ps1') -OutputDirectory $OutputDirectory; if ($LASTEXITCODE -ne 0) { throw 'Plugin build failed.' } }
$version = '1.2.0'
$source = Join-Path $root 'installer'
$build = Join-Path $source ('build\release-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Force -Path $build
$payload = Join-Path $build 'payload'
$null = New-Item -ItemType Directory -Force -Path $payload
foreach ($name in @('Installer.Core.psm1', 'Install-Engine.ps1', 'SetupWizard.ps1', 'vendor_runtime.py', 'prepare_veyra.py', 'Test-Runtime.py', 'downloads.lock.json')) {
    Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $payload $name)
}
foreach ($name in @('INSTALL-WINDOWS.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE')) { Copy-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $payload $name) }
$pluginSource = Join-Path $OutputDirectory 'VSR-Everywhere'
Copy-Item -LiteralPath $pluginSource -Destination (Join-Path $payload 'plugin') -Recurse
$hashes = [ordered]@{}
foreach ($file in Get-ChildItem -LiteralPath (Join-Path $payload 'plugin') -File -Recurse) {
    $relative = $file.FullName.Substring((Join-Path $payload 'plugin').Length + 1).Replace('\', '/')
    $hashes[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$hashes | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $payload 'payload.sha256.json') -Encoding UTF8
# Windows PowerShell 5.1 needs a BOM for UTF-8 source files.
foreach ($file in Get-ChildItem -LiteralPath $payload -File | Where-Object { $_.Extension -in @('.ps1', '.psm1') }) {
    [IO.File]::WriteAllText($file.FullName, [IO.File]::ReadAllText($file.FullName), (New-Object Text.UTF8Encoding($true)))
}
$dist = $OutputDirectory
Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination (Join-Path $dist 'LICENSE') -Force
$bundleName = "VSR-Everywhere-$version-Windows.zip"
$bundle = Join-Path $dist $bundleName
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression
if (Test-Path -LiteralPath $bundle) { Remove-Item -LiteralPath $bundle -Force }
[IO.Compression.ZipFile]::CreateFromDirectory($payload, $bundle, [IO.Compression.CompressionLevel]::Optimal, $false)
$sha = (Get-FileHash -LiteralPath $bundle -Algorithm SHA256).Hash.ToLowerInvariant()
$wrapper = (Get-Content -LiteralPath (Join-Path $source 'Install-VSREverywhere.ps1.in') -Raw).Replace('__BUNDLE_FILE__', $bundleName).Replace('__BUNDLE_SHA256__', $sha)
$scriptPath = Join-Path $dist 'Install-VSREverywhere.ps1'
[IO.File]::WriteAllText($scriptPath, $wrapper, (New-Object Text.UTF8Encoding($true)))
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { throw 'The Windows .NET Framework C# compiler was not found.' }
$exe = Join-Path $dist "VSR-Everywhere-$version-Setup.exe"
$automation = @(Get-ChildItem -LiteralPath (Join-Path $env:WINDIR 'Microsoft.NET\assembly\GAC_MSIL\System.Management.Automation') -Recurse -Filter System.Management.Automation.dll)[0].FullName
& $csc '/nologo' '/target:winexe' '/platform:x64' '/optimize+' "/out:$exe" "/win32manifest:$(Join-Path $source 'setup.manifest')" "/resource:$scriptPath,Install-VSREverywhere.ps1" "/resource:$bundle,$bundleName" '/reference:System.Windows.Forms.dll' '/reference:System.Security.dll' "/reference:$automation" (Join-Path $source 'SetupBootstrap.cs')
if ($LASTEXITCODE -ne 0) { throw 'Setup executable compilation failed.' }
$sourceZip = Join-Path $dist "VSR-Everywhere-$version-Source.zip"
if (Test-Path -LiteralPath $sourceZip) { Remove-Item -LiteralPath $sourceZip -Force }
$stream = [IO.File]::Create($sourceZip)
$archive = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Create)
try {
    $files = @(Get-ChildItem -LiteralPath $root -File | Where-Object { $_.Name -notlike 'HANDOFF-*' -and ($_.Extension -in @('.ps1', '.md') -or $_.Name -in @('NuGet.Config', '.gitignore', 'LICENSE')) })
    foreach ($folder in @('src', 'worker', 'tests', 'installer', 'tools', 'docs', 'native')) {
        $files += @(Get-ChildItem -LiteralPath (Join-Path $root $folder) -File -Recurse | Where-Object {
            $_.FullName -notmatch '[\\/](bin|obj|build|work|__pycache__|jobs)[\\/]' -and
            ($_.Extension -in @('.cs', '.csproj', '.html', '.py', '.cpp', '.h', '.cmake', '.patch', '.txt', '.cjs', '.ps1', '.psm1', '.in', '.json', '.manifest', '.md', '.webp') -or $_.Name -eq 'LICENSE')
        })
    }
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($root.Length + 1).Replace('\', '/')
        $null = [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $file.FullName, $relative, [IO.Compression.CompressionLevel]::Optimal)
    }
} finally { $archive.Dispose(); $stream.Dispose() }
Get-ChildItem -LiteralPath $dist -File | Where-Object { $_.Name -in @($bundleName, 'Install-VSREverywhere.ps1', [IO.Path]::GetFileName($exe), [IO.Path]::GetFileName($sourceZip)) } | ForEach-Object {
    Write-Host ('Built: ' + $_.FullName) -ForegroundColor Green
}
