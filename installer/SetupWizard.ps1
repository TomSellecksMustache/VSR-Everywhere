[CmdletBinding()]
param([switch]$Preview, [switch]$Silent, [switch]$Embedded, [string]$DataPath = '', [string]$ServerPath = '', [string]$InstallFolder = '')
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -Global
Import-Module (Join-Path $PSScriptRoot 'Installer.Core.psm1') -Force
if ([string]::IsNullOrWhiteSpace($InstallFolder)) { $InstallFolder = $env:ProgramData }

function New-SetupJob($Target, [bool]$AllDevices, [string]$Folder) {
    $root = New-SetupStorageRoot $Folder
    $path = Join-Path $root ('jobs\' + [Guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Force -Path $path
    @{ Product = 'VSR Everywhere'; Version = '1.0.0'; DataPath = $Target.DataPath;
       ServerPath = $Target.ServerPath; AllDevices = $AllDevices; InstallFolder = $Folder } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $path 'plan.json') -Encoding UTF8
    return $path
}

function Get-DefaultAllDevices($Target) {
    $snapshot = Get-PluginSnapshot $Target.DataPath
    if (-not $snapshot.Config) { return $true }
    $all = Get-XmlValue $snapshot.Config 'EnableAllDevices'
    if ($all -eq 'true') { return $true }
    $ids = @($snapshot.Config.DocumentElement.SelectNodes('EnabledDeviceIds/string'))
    return $ids.Count -eq 0
}

if ($Silent) {
    if ($Preview) { throw 'Preview and silent installation cannot be combined.' }
    if ($DataPath -and $ServerPath) { $target = Get-JellyfinTarget $DataPath $ServerPath }
    else {
        $targets = @(Get-JellyfinInstallations)
        if ($targets.Count -ne 1) { throw 'Silent setup requires one detected Jellyfin server, or explicit -DataPath and -ServerPath arguments.' }
        $target = $targets[0]
    }
    $job = New-SetupJob $target (Get-DefaultAllDevices $target) $InstallFolder
    $code = Invoke-SetupProcess (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'Install-Engine.ps1'), '-JobPath', $job) $job 14400 -NoCancel
    if ($Embedded) { return $code }
    exit $code
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$script:pageIndex = 0; $script:target = $null; $script:engine = $null; $script:job = ''; $script:installing = $false
$script:exitCode = 2; $script:result = $null
$form = New-Object Windows.Forms.Form
$form.Text = 'VSR Everywhere 1.0.0 Setup'
$form.ClientSize = New-Object Drawing.Size(760, 550)
$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedSingle'; $form.MaximizeBox = $false
$form.AutoScaleMode = 'Dpi'; $form.BackColor = [Drawing.Color]::White
$form.Font = New-Object Drawing.Font('Segoe UI', 10)

function Add-Label($Parent, [string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height, [int]$Size = 10, [switch]$Bold) {
    $label = New-Object Windows.Forms.Label
    $label.Text = $Text; $label.Location = New-Object Drawing.Point($X, $Y); $label.Size = New-Object Drawing.Size($Width, $Height)
    $style = if ($Bold) { [Drawing.FontStyle]::Bold } else { [Drawing.FontStyle]::Regular }
    $label.Font = New-Object Drawing.Font('Segoe UI', $Size, $style)
    $Parent.Controls.Add($label); return $label
}
function Add-Link($Parent, [string]$Text, [string]$Url, [int]$X, [int]$Y, [int]$Width = 220) {
    $link = New-Object Windows.Forms.LinkLabel
    $link.Text = $Text; $link.Location = New-Object Drawing.Point($X, $Y); $link.Size = New-Object Drawing.Size($Width, 26)
    $link.Tag = $Url
    $link.add_LinkClicked({ param($sender, $eventArgs) Start-Process -FilePath ([string]$sender.Tag) })
    $Parent.Controls.Add($link); return $link
}
$null = Add-Label $form 'VSR Everywhere' 24 18 650 38 22 -Bold
$null = Add-Label $form '1.0.0  |  Windows setup' 26 61 650 26 10
$divider = New-Object Windows.Forms.Label
$divider.Location = New-Object Drawing.Point(0, 98); $divider.Size = New-Object Drawing.Size(760, 1); $divider.BackColor = [Drawing.Color]::Gainsboro
$form.Controls.Add($divider)
$pages = @()
for ($i = 0; $i -lt 5; $i++) {
    $panel = New-Object Windows.Forms.Panel
    $panel.Location = New-Object Drawing.Point(26, 116); $panel.Size = New-Object Drawing.Size(708, 365); $panel.Visible = $false
    $form.Controls.Add($panel); $pages += $panel
}
$null = Add-Label $pages[0] 'Better video, with setup taken care of.' 0 0 700 34 16 -Bold
$null = Add-Label $pages[0] "Setup finds your Jellyfin server, downloads the required components, and checks that NVIDIA VSR and video encoding work.`r`n`r`nIt then configures the plugin and briefly restarts Jellyfin. Your media library stays in place." 0 48 680 126
$null = Add-Label $pages[0] 'You will need' 0 185 650 26 11 -Bold
$null = Add-Label $pages[0] "Jellyfin 12.1 on this PC, Windows 10/11 x64, and a supported NVIDIA GPU with driver 570.65 or newer.`r`n`r`nAn internet connection and at least 25 GB free on your chosen drive. GPU library downloads can total several gigabytes." 0 216 680 98
$null = Add-Link $pages[0] 'NVIDIA component license terms' 'https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/' 0 333 290
$null = Add-Link $pages[0] 'Jellyfin downloads' 'https://jellyfin.org/downloads/windows/' 348 333 200

$null = Add-Label $pages[1] 'Your Jellyfin server' 0 0 700 34 16 -Bold
$null = Add-Label $pages[1] 'Setup selected the detected server. For most installations, select Next.' 0 44 700 38
$serverList = New-Object Windows.Forms.ComboBox
$serverList.DropDownStyle = 'DropDownList'; $serverList.Location = New-Object Drawing.Point(0, 92); $serverList.Size = New-Object Drawing.Size(700, 30)
$pages[1].Controls.Add($serverList)
$advanced = New-Object Windows.Forms.CheckBox
$advanced.Text = 'Choose a custom Jellyfin location'; $advanced.Location = New-Object Drawing.Point(0, 140); $advanced.Size = New-Object Drawing.Size(600, 28)
$pages[1].Controls.Add($advanced)
$null = Add-Label $pages[1] 'Jellyfin data folder' 0 185 620 24
$dataBox = New-Object Windows.Forms.TextBox
$dataBox.Location = New-Object Drawing.Point(0, 214); $dataBox.Size = New-Object Drawing.Size(600, 28); $dataBox.Enabled = $false
$pages[1].Controls.Add($dataBox)
$dataBrowse = New-Object Windows.Forms.Button
$dataBrowse.Text = 'Browse...'; $dataBrowse.Location = New-Object Drawing.Point(610, 213); $dataBrowse.Size = New-Object Drawing.Size(90, 30); $dataBrowse.Enabled = $false
$pages[1].Controls.Add($dataBrowse)
$null = Add-Label $pages[1] 'Jellyfin Server executable' 0 258 620 24
$serverBox = New-Object Windows.Forms.TextBox
$serverBox.Location = New-Object Drawing.Point(0, 287); $serverBox.Size = New-Object Drawing.Size(600, 28); $serverBox.Enabled = $false
$pages[1].Controls.Add($serverBox)
$serverBrowse = New-Object Windows.Forms.Button
$serverBrowse.Text = 'Browse...'; $serverBrowse.Location = New-Object Drawing.Point(610, 286); $serverBrowse.Size = New-Object Drawing.Size(90, 30); $serverBrowse.Enabled = $false
$pages[1].Controls.Add($serverBrowse)
$detectionError = Add-Label $pages[1] '' 0 329 700 36
$detectionError.ForeColor = [Drawing.Color]::Firebrick

$null = Add-Label $pages[2] 'Ready to install' 0 0 700 34 16 -Bold
$summary = Add-Label $pages[2] '' 0 49 700 120
$null = Add-Label $pages[2] 'Download and runtime folder' 0 174 700 24
$installBox = New-Object Windows.Forms.TextBox
$installBox.Text = $InstallFolder; $installBox.Location = New-Object Drawing.Point(0, 202); $installBox.Size = New-Object Drawing.Size(600, 28)
$pages[2].Controls.Add($installBox)
$installBrowse = New-Object Windows.Forms.Button
$installBrowse.Text = 'Browse...'; $installBrowse.Location = New-Object Drawing.Point(610, 201); $installBrowse.Size = New-Object Drawing.Size(90, 30)
$pages[2].Controls.Add($installBrowse)
$storageNote = Add-Label $pages[2] '' 0 239 700 48
$allDevices = New-Object Windows.Forms.CheckBox
$allDevices.Text = 'Enable VSR for all Jellyfin devices'; $allDevices.Checked = $true
$allDevices.Location = New-Object Drawing.Point(0, 293); $allDevices.Size = New-Object Drawing.Size(690, 30)
$pages[2].Controls.Add($allDevices)
$null = Add-Label $pages[2] 'Device selection, resolutions, and quality can be changed in Jellyfin afterward.' 0 329 690 34

$progressTitle = Add-Label $pages[3] 'Preparing setup...' 0 0 700 36 14 -Bold
$progress = New-Object Windows.Forms.ProgressBar
$progress.Location = New-Object Drawing.Point(0, 48); $progress.Size = New-Object Drawing.Size(700, 22); $progress.Style = 'Continuous'
$pages[3].Controls.Add($progress)
$logBox = New-Object Windows.Forms.TextBox
$logBox.Location = New-Object Drawing.Point(0, 91); $logBox.Size = New-Object Drawing.Size(700, 264)
$logBox.Multiline = $true; $logBox.ReadOnly = $true; $logBox.ScrollBars = 'Vertical'
$logBox.Font = New-Object Drawing.Font('Consolas', 9); $logBox.BackColor = [Drawing.Color]::FromArgb(247, 248, 250)
$pages[3].Controls.Add($logBox)

$finishTitle = Add-Label $pages[4] 'VSR Everywhere is ready' 0 0 700 36 16 -Bold
$finishText = Add-Label $pages[4] '' 0 55 700 194
$openJellyfin = New-Object Windows.Forms.CheckBox
$openJellyfin.Text = 'Open VSR Everywhere settings in Jellyfin'; $openJellyfin.Checked = $true
$openJellyfin.Location = New-Object Drawing.Point(0, 269); $openJellyfin.Size = New-Object Drawing.Size(690, 30)
$pages[4].Controls.Add($openJellyfin)
$openLog = New-Object Windows.Forms.LinkLabel
$openLog.Text = 'Open setup log'; $openLog.Location = New-Object Drawing.Point(0, 321); $openLog.Size = New-Object Drawing.Size(250, 26)
$openLog.add_LinkClicked({ if ($script:job) { Start-Process notepad.exe -ArgumentList (ConvertTo-NativeArgument (Join-Path $script:job 'setup.log')) } })
$pages[4].Controls.Add($openLog)

$footer = New-Object Windows.Forms.Label
$footer.Location = New-Object Drawing.Point(0, 492); $footer.Size = New-Object Drawing.Size(760, 1); $footer.BackColor = [Drawing.Color]::Gainsboro
$form.Controls.Add($footer)
$back = New-Object Windows.Forms.Button
$back.Text = '< Back'; $back.Location = New-Object Drawing.Point(430, 509); $back.Size = New-Object Drawing.Size(96, 30)
$form.Controls.Add($back)
$next = New-Object Windows.Forms.Button
$next.Text = 'Next >'; $next.Location = New-Object Drawing.Point(538, 509); $next.Size = New-Object Drawing.Size(96, 30)
$form.Controls.Add($next); $form.AcceptButton = $next
$cancel = New-Object Windows.Forms.Button
$cancel.Text = 'Cancel'; $cancel.Location = New-Object Drawing.Point(646, 509); $cancel.Size = New-Object Drawing.Size(88, 30)
$form.Controls.Add($cancel)

function Show-SetupPage([int]$Index) {
    $script:pageIndex = $Index
    for ($j = 0; $j -lt $pages.Count; $j++) { $pages[$j].Visible = $j -eq $Index }
    $back.Enabled = $Index -gt 0 -and $Index -lt 3
    $next.Enabled = $Index -ne 3
    $next.Text = if ($Index -eq 2) { 'Install' } elseif ($Index -eq 4) { 'Finish' } else { 'Next >' }
    $cancel.Visible = $Index -ne 4
}
$serverList.add_SelectedIndexChanged({
    if ($serverList.SelectedIndex -ge 0 -and $serverList.SelectedIndex -lt $script:targets.Count) {
        $selected = $script:targets[$serverList.SelectedIndex]
        $dataBox.Text = $selected.DataPath; $serverBox.Text = $selected.ServerPath
    }
})
$advanced.add_CheckedChanged({ $dataBox.Enabled = $advanced.Checked; $serverBox.Enabled = $advanced.Checked; $dataBrowse.Enabled = $advanced.Checked; $serverBrowse.Enabled = $advanced.Checked; $serverList.Enabled = -not $advanced.Checked })
$dataBrowse.add_Click({
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Choose the Jellyfin data folder containing config\system.xml'
    if ($dialog.ShowDialog() -eq 'OK') { $dataBox.Text = $dialog.SelectedPath }
    $dialog.Dispose()
})
$serverBrowse.add_Click({
    $dialog = New-Object Windows.Forms.OpenFileDialog
    $dialog.Title = 'Choose Jellyfin Server'; $dialog.Filter = 'Jellyfin Server|jellyfin.exe'
    if ($dialog.ShowDialog() -eq 'OK') { $serverBox.Text = $dialog.FileName }
    $dialog.Dispose()
})
$back.add_Click({ Show-SetupPage ($script:pageIndex - 1) })
function Update-StorageNote {
    try {
        $root = Get-SetupStorageRoot $installBox.Text
        $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($root))
        $storageNote.Text = $root + "`r`n" + [Math]::Round($drive.AvailableFreeSpace / 1GB, 1) + ' GB free. A fresh installation needs 25 GB.'
        $storageNote.ForeColor = [Drawing.Color]::Black
    } catch { $storageNote.Text = $_.Exception.Message; $storageNote.ForeColor = [Drawing.Color]::Firebrick }
}
$installBox.add_TextChanged({ Update-StorageNote })
$installBrowse.add_Click({
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Choose a drive or folder with 25 GB free. Setup creates a VsrEverywhere subfolder.'
    $dialog.SelectedPath = $installBox.Text
    if ($dialog.ShowDialog() -eq 'OK') { $installBox.Text = $dialog.SelectedPath }
    $dialog.Dispose()
})
Update-StorageNote
$cancel.add_Click({
    if ($script:installing) {
        New-Item -ItemType File -Force -Path (Join-Path $script:job 'cancel.request') | Out-Null
        $cancel.Enabled = $false; $progressTitle.Text = 'Cancelling safely...'
    } else { $form.Close() }
})
$form.add_FormClosing({ param($sender, $eventArgs) if ($script:installing) { $eventArgs.Cancel = $true } })

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 400
$timer.add_Tick({
    if (-not $script:installing) { return }
    if ($Preview) {
        $script:installing = $false; $timer.Stop()
        $finishTitle.Text = 'Setup preview complete'
        $finishText.Text = 'This is a preview of VSR Everywhere 1.0.0 setup. No downloads, installation, or server changes were made.'
        $openJellyfin.Visible = $false; $openLog.Visible = $false
        Show-SetupPage 4
        return
    }
    try {
        $statusPath = Join-Path $script:job 'status.json'
        if (Test-Path -LiteralPath $statusPath) {
            $status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
            $progressTitle.Text = $status.Message; $progress.Value = [Math]::Max(0, [Math]::Min(100, [int]$status.Percent))
            $cancel.Enabled = $status.Stage -ne 'install' -and -not (Test-Path -LiteralPath (Join-Path $script:job 'cancel.request'))
        }
        $log = Join-Path $script:job 'setup.log'
        if (Test-Path -LiteralPath $log) {
            $text = Get-Content -LiteralPath $log -Raw
            if ($text -ne $logBox.Text) { $logBox.Text = $text; $logBox.SelectionStart = $logBox.TextLength; $logBox.ScrollToCaret() }
        }
        if (-not $script:engine.HasExited) { return }
        $timer.Stop(); $script:installing = $false
        $resultPath = Join-Path $script:job 'result.json'
        if (Test-Path -LiteralPath $resultPath) { $script:result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json }
        else { $script:result = [pscustomobject]@{ Success = $false; Message = 'Setup stopped unexpectedly. See the log for details.'; BackupPath = ''; Url = '' } }
        if ($script:result.Success) {
            $script:exitCode = 0
            $finishText.Text = "VSR Everywhere 1.0.0 is installed and loaded by Jellyfin.`r`n`r`nNVIDIA VSR, HEVC video, AAC audio, and HLS checks passed.`r`n`r`nYour device and resolution settings are ready to review in Jellyfin."
        } else {
            $finishTitle.Text = 'Setup could not finish'
            $finishText.Text = $script:result.Message
            if ($script:result.BackupPath) { $finishText.Text += "`r`n`r`nBackup: " + $script:result.BackupPath }
            $openJellyfin.Visible = $false; $script:exitCode = 1
        }
        Show-SetupPage 4
    } catch { $progressTitle.Text = 'Finishing the current setup step...' }
})
$next.add_Click({
    try {
        switch ($script:pageIndex) {
            0 { Show-SetupPage 1 }
            1 {
                if ($Preview) { $script:target = $script:targets[0]; $gpu = 'NVIDIA RTX GPU - compatibility check passed'; $allDevices.Checked = $true }
                else { $script:target = Get-JellyfinTarget $dataBox.Text $serverBox.Text; $gpu = Get-NvidiaStatus; $allDevices.Checked = Get-DefaultAllDevices $script:target }
                $summary.Text = "Jellyfin " + $script:target.Version + "`r`n" + $script:target.DataPath + "`r`n`r`n" + $gpu
                $detectionError.Text = ''; Show-SetupPage 2
            }
            2 {
                $null = Get-SetupStorageRoot $installBox.Text
                $script:installing = $true; $cancel.Enabled = $true
                Show-SetupPage 3
                if (-not $Preview) {
                    $script:job = New-SetupJob $script:target $allDevices.Checked $installBox.Text
                    $psi = New-Object Diagnostics.ProcessStartInfo
                    $psi.FileName = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
                    $psi.Arguments = (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'Install-Engine.ps1'), '-JobPath', $script:job) | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
                    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
                    $script:engine = [Diagnostics.Process]::Start($psi)
                }
                $timer.Start()
            }
            4 {
                if ($openJellyfin.Visible -and $openJellyfin.Checked -and $script:result -and $script:result.Success) {
                    Start-Process -FilePath ($script:result.Url + '/web/#/configurationpage?name=VSR%20Everywhere')
                }
                $form.Close()
            }
        }
    } catch {
        $script:installing = $false
        if ($script:pageIndex -ge 2) { $storageNote.Text = $_.Exception.Message; $storageNote.ForeColor = [Drawing.Color]::Firebrick; Show-SetupPage 2 }
        else { $detectionError.Text = $_.Exception.Message; Show-SetupPage 1 }
    }
})
if ($Preview) {
    $script:targets = @([pscustomobject]@{ DataPath = 'C:\ProgramData\Jellyfin\Server'; ServerPath = 'C:\Program Files\Jellyfin\Server\jellyfin.exe'; Version = '12.1.0'; DisplayName = 'Jellyfin 12.1.0 - C:\ProgramData\Jellyfin\Server' })
} else {
    $script:targets = @(Get-JellyfinInstallations)
    if ($DataPath -and $ServerPath) { $script:targets = @(Get-JellyfinTarget $DataPath $ServerPath) }
}
foreach ($target in $script:targets) { $null = $serverList.Items.Add($target.DisplayName) }
if ($script:targets.Count) { $serverList.SelectedIndex = 0 }
else { $advanced.Checked = $true; $detectionError.Text = 'No initialized Jellyfin 12.1 server was found. Choose its folders, or install Jellyfin first.' }
Show-SetupPage 0
$null = $form.ShowDialog()
$timer.Stop(); $timer.Dispose(); $form.Dispose()
if ($script:engine) { $script:engine.Dispose() }
if ($Embedded) { return $script:exitCode }
exit $script:exitCode
