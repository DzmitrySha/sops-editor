# Creates a desktop shortcut to start-windows.cmd.
# Run once:  powershell -ExecutionPolicy Bypass -File windows\install-shortcut.ps1

$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root "start-windows.cmd"
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop "SOPS Editor.lnk"))
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $root
$shortcut.Description = "SOPS secrets editor in Docker"
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,48"
$shortcut.Save()
Write-Host "Shortcut created: $desktop\SOPS Editor.lnk"
