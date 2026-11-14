[CmdletBinding()]
param([switch]$NoRestore, [string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -Global
$root = $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root 'dist' }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $root 'dist')).TrimEnd('\')
if ($outputRoot -ne $allowedRoot -and -not $outputRoot.StartsWith($allowedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Build output must stay inside this project dist folder.' }
& (Join-Path $root 'Build-Veyra.ps1')
$project = Join-Path $root 'src\Jellyfin.Plugin.VsrEverywhere\Jellyfin.Plugin.VsrEverywhere.csproj'
$cfg = Join-Path $root 'NuGet.Config'
$dll = Join-Path $root 'src\Jellyfin.Plugin.VsrEverywhere\bin\Release\net8.0\Jellyfin.Plugin.VsrEverywhere.dll'
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw 'Install .NET 10 SDK.' }
if (-not $NoRestore) {
    & dotnet restore $project --configfile $cfg
    if ($LASTEXITCODE -ne 0) { throw 'NuGet restore failed.' }
}
& dotnet build $project -c Release -f net8.0 --no-restore --no-incremental
if ($LASTEXITCODE -ne 0) { throw '.NET build failed.' }
if (-not (Test-Path -LiteralPath $dll -PathType Leaf)) { throw "DLL missing: $dll" }
$assembly = [xml](Get-Content -LiteralPath $project -Raw)
$version = [string]$assembly.Project.PropertyGroup.Version
if ([string]::IsNullOrWhiteSpace($version)) { throw 'Could not determine plugin version.' }
$dist = Join-Path $outputRoot 'VSR-Everywhere'
if (Test-Path -LiteralPath $dist) {
    $full = [IO.Path]::GetFullPath($dist)
    $distRoot = [IO.Path]::GetFullPath((Join-Path $root 'dist')).TrimEnd('\') + '\'
    if (-not $full.StartsWith($distRoot, [StringComparison]::OrdinalIgnoreCase) -or
        ((Get-Item -LiteralPath $full -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Invalid build output directory.' }
    Remove-Item -LiteralPath $full -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $dist | Out-Null
Copy-Item -LiteralPath $dll -Destination (Join-Path $dist 'Jellyfin.Plugin.VsrEverywhere.dll') -Force
foreach ($name in @('LICENSE', 'THIRD_PARTY_NOTICES.md')) { Copy-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $dist $name) }
New-Item -ItemType Directory -Force -Path (Join-Path $dist 'worker') | Out-Null
Get-ChildItem -LiteralPath (Join-Path $root 'worker') -Filter '*.py' -File | Copy-Item -Destination (Join-Path $dist 'worker') -Force
Copy-Item -LiteralPath (Join-Path $root 'installer\build\veyra-component') -Destination (Join-Path $dist 'worker\veyra') -Recurse
$metadata = [ordered]@{
    category = 'General'
    guid = '7ca129d0-7454-41c7-aad6-55250b1a7b5b'
    name = 'VSR Everywhere'
    version = ($version + $(if ($version.Split('.').Count -eq 3) { '.0' } else { '' }))
    status = 'Active'
    targetAbi = '10.10.0.0'
    description = 'NVIDIA Video Super Resolution with selectable 1080p to 8K processing and HEVC streaming for selected Jellyfin devices.'
    changelog = 'Neural rendering, automatic content profiles, source-rate interpolation, and improved startup and color handling. Auto HDR remains experimental.'
}
[IO.File]::WriteAllText((Join-Path $dist 'meta.json'), ($metadata | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
$zip = Join-Path $outputRoot "VSR-Everywhere-$version-plugin.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $dist '*') -DestinationPath $zip -Force
Write-Host "Built plugin directory: $dist" -ForegroundColor Green
Write-Host "Built plugin ZIP:       $zip" -ForegroundColor Green
