$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$appName = "Talos"
$sourceExe = Join-Path $root "dist\$appName.exe"
$installDir = Join-Path $env:LOCALAPPDATA "Programs\$appName"

if (-not (Test-Path -LiteralPath $sourceExe)) {
  Write-Host "Build output not found. Run .\build_app.ps1 first."
  exit 1
}

if (Test-Path -LiteralPath $installDir) {
  Remove-Item -LiteralPath $installDir -Recurse -Force
}

New-Item -ItemType Directory -Path $installDir -Force | Out-Null
Copy-Item -LiteralPath $sourceExe -Destination (Join-Path $installDir "$appName.exe") -Force
Copy-Item -LiteralPath (Join-Path $root "config.json") -Destination (Join-Path $installDir "config.json") -Force
Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination (Join-Path $installDir "README.md") -Force

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "$appName.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $installDir "$appName.exe"
$shortcut.WorkingDirectory = $installDir
$shortcut.Description = "Talos"
$shortcut.Save()

Write-Host "Installed:"
Write-Host (Join-Path $installDir "$appName.exe")
Write-Host "Shortcut:"
Write-Host $shortcutPath
