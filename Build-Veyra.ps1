[CmdletBinding()]
param([string]$CMake = '', [string]$WorkDirectory = '')
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -Global
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Global
if (-not $WorkDirectory) { $WorkDirectory = Join-Path $PSScriptRoot 'installer\build' }
if (-not $CMake) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere) {
        foreach ($installation in @(& $vswhere -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath)) {
            $candidate = Join-Path $installation 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
            if (Test-Path -LiteralPath $candidate) { $CMake = $candidate; break }
        }
    }
    if (-not $CMake) { $CMake = 'cmake' }
}
$work = [IO.Path]::GetFullPath($WorkDirectory)
$null = New-Item -ItemType Directory -Force -Path $work
$deps = Join-Path $work 'veyra-deps'
$null = New-Item -ItemType Directory -Force -Path $deps
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed ($LASTEXITCODE). See the preceding diagnostic." }
}
function Get-PinnedFile([string]$Url, [string]$Path, [string]$Sha256) {
    if (-not (Test-Path -LiteralPath $Path)) {
        $null = New-Item -ItemType Directory -Force -Path (Split-Path $Path)
        Invoke-WebRequest -Uri $Url -OutFile ($Path + '.partial') -UseBasicParsing
        if ((Get-FileHash -LiteralPath ($Path + '.partial') -Algorithm SHA256).Hash -ine $Sha256) { throw "Checksum mismatch: $Url" }
        Move-Item -LiteralPath ($Path + '.partial') -Destination $Path
    }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ine $Sha256) { throw "Checksum mismatch: $Path" }
}
function Get-PinnedRepository([string]$Url, [string]$Path, [string]$Commit, [string[]]$Sparse = @()) {
    if (-not (Test-Path -LiteralPath (Join-Path $Path '.git'))) {
        Invoke-Checked git @('clone', '--filter=blob:none', '--no-checkout', $Url, $Path)
        if ($Sparse.Count) { Invoke-Checked git (@('-C', $Path, 'sparse-checkout', 'set') + $Sparse) }
        Invoke-Checked git @('-C', $Path, 'fetch', '--depth', '1', 'origin', $Commit)
        Invoke-Checked git @('-C', $Path, 'checkout', '--detach', $Commit)
    }
    $actual = & git -C $Path rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $actual -ne $Commit) { throw "Unexpected source revision in $Path. Choose a fresh work directory." }
}
$veyra = Join-Path $work 'veyra-upstream'
$commit = '8332c8a8382242096a2699d3ba4dd52ad47bbd74'
Get-PinnedRepository 'https://github.com/Likely7/Veyra-NRVideo.git' $veyra $commit
$sdk = Join-Path $deps 'dlss-sdk'
Get-PinnedRepository 'https://github.com/NVIDIA/DLSS.git' $sdk '9981c6350dc58c0ce21f024fd65e31e851c31689' @('include', 'lib/Windows_x86_64/x64')
$headers = @{
    'nvOpticalFlowCommon.h' = 'f133879f7add54d42d71f5d7839a05962af7aaa3620909e3cbfbece77119d30f'
    'nvOpticalFlowD3D11.h' = '4721a36321591e6882e68ad342f5113a64e6c55f066b563e205fd9cc1a91cae3'
    'nvOpticalFlowD3D12.h' = '0b9e114f8e05140c6cd551ef1a7bf772462dfcd9424045081910f57cb68ccd33'
}
$nvof = Join-Path $deps 'nvof'
foreach ($name in $headers.Keys) {
    Get-PinnedFile "https://raw.githubusercontent.com/AlanBacker/VRChat-DLSS5-Cam/0ad343a576c3624c185f503b3cb15be229db213e/third_party/nvof/$name" (Join-Path $nvof "NvOFInterface\$name") $headers[$name]
}
$ffmpegArchive = Join-Path $deps 'ffmpeg-9.0.2-full_build-shared.7z'
Get-PinnedFile 'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-full_build-shared.7z' $ffmpegArchive '4d2060a8b34a940aa47d785142055bb92a63053781e55f2ace4546edd519a8f5'
$ffmpeg = Join-Path $deps 'ffmpeg-9.0.2-full_build-shared'
if (-not (Test-Path -LiteralPath (Join-Path $ffmpeg 'lib\avutil.lib'))) {
    Invoke-Checked (Join-Path $env:WINDIR 'System32\tar.exe') @('-xf', $ffmpegArchive, '-C', $deps)
}
$module = Join-Path $ffmpeg 'share\ffmpeg'
$null = New-Item -ItemType Directory -Force -Path $module
$libraries = @('avutil', 'avcodec', 'avformat', 'avfilter', 'swscale', 'swresample') | ForEach-Object { (Join-Path $ffmpeg "lib\$_.lib").Replace('\', '/') }
$find = "set(FFMPEG_FOUND TRUE)`nset(FFMPEG_INCLUDE_DIRS `"$($ffmpeg.Replace('\', '/'))/include`")`nset(FFMPEG_LIBRARIES `"$($libraries -join ';')`")`n"
[IO.File]::WriteAllText((Join-Path $module 'FindFFMPEG.cmake'), $find)
$cmakeFile = Join-Path $veyra 'CMakeLists.txt'
$original = [IO.File]::ReadAllText($cmakeFile)
if (-not $original.Contains('if(VSR_VEYRA_FILTER_ONLY)')) {
    $anchor = '# veyra_media: FFmpeg demux/decode'
    if ($original.IndexOf($anchor) -lt 0 -or $original.IndexOf($anchor) -ne $original.LastIndexOf($anchor)) { throw 'Pinned Veyra headless build anchor changed.' }
    [IO.File]::WriteAllText($cmakeFile, $original.Replace($anchor, "if(VSR_VEYRA_FILTER_ONLY)`n  return()`nendif()`n" + $anchor))
}
$build = Join-Path $work 'veyra-native'
Invoke-Checked $CMake @('-S', (Join-Path $PSScriptRoot 'native\veyra'), '-B', $build, '-G', 'Visual Studio 17 2022', '-A', 'x64',
    "-DVEYRA_SOURCE_DIR=$veyra", "-DVEYRA_DLSS_SDK_ROOT=$sdk", "-DVEYRA_NVOF_SDK_ROOT=$nvof", "-DVEYRA_FFMPEG_ROOT=$ffmpeg")
Invoke-Checked $CMake @('--build', $build, '--config', 'Release', '--target', 'VsrVeyraFilter', '--parallel', '4')
$component = Join-Path $work 'veyra-component'
$null = New-Item -ItemType Directory -Force -Path $component
Copy-Item -LiteralPath (Join-Path $build 'Release\VsrVeyraFilter.exe') -Destination $component -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'native\veyra\LICENSE') -Destination $component -Force
# The GPL worker ships its complete corresponding source, independently of the MIT plugin.
$sourceStage = Join-Path $work ('veyra-source-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Force -Path $sourceStage
Invoke-Checked git @('-C', $veyra, 'archive', '--format=zip', "--output=$(Join-Path $sourceStage 'veyra-upstream.zip')", $commit)
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'native') -Destination $sourceStage -Recurse
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Build-Veyra.ps1') -Destination $sourceStage
Copy-Item -LiteralPath (Join-Path $veyra 'THIRD_PARTY_NOTICES.md') -Destination (Join-Path $sourceStage 'UPSTREAM-NOTICES.md')
Add-Type -AssemblyName System.IO.Compression.FileSystem
$sourceZip = Join-Path $component 'VsrVeyraFilter-source.zip'
if (Test-Path -LiteralPath $sourceZip) { Remove-Item -LiteralPath $sourceZip -Force }
[IO.Compression.ZipFile]::CreateFromDirectory($sourceStage, $sourceZip)
Write-Host "Built GPL Veyra component and corresponding source: $component"
