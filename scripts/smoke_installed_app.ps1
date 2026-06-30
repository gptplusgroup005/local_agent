param(
  [switch]$AllowDirty,
  [switch]$SkipBuild,
  [switch]$KeepInstalled,
  [switch]$SkipLaunch,
  [switch]$ManualArduinoConfirmed,
  [int]$TimeoutSec = 30
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
Set-Location $root
$ErrorActionPreference = "Stop"

function Get-Slug([string]$value) {
  return ($value.Trim().ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
}

function Invoke-ProcessChecked([string]$FilePath, [string[]]$ArgumentList, [string]$Label) {
  $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -Wait -PassThru -WindowStyle Hidden
  if ($process.ExitCode -ne 0) {
    throw "$Label failed with exit code $($process.ExitCode)"
  }
}

function Stop-InstalledTalos([object]$Process, [string]$InstallDir) {
  if ($Process -and -not $Process.HasExited) {
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    Wait-Process -Id $Process.Id -Timeout 5 -ErrorAction SilentlyContinue
  }

  $escapedInstallDir = [System.IO.Path]::GetFullPath($InstallDir)
  Get-Process -Name $script:appName -ErrorAction SilentlyContinue |
    Where-Object {
      $_.Path -and [System.IO.Path]::GetFullPath($_.Path).StartsWith($escapedInstallDir)
    } |
    ForEach-Object {
      Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
      Wait-Process -Id $_.Id -Timeout 5 -ErrorAction SilentlyContinue
    }
}

function Remove-TreeWithRetry([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }
  for ($attempt = 1; $attempt -le 8; $attempt++) {
    try {
      Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
      return
    } catch {
      if ($attempt -eq 8) {
        throw
      }
      Start-Sleep -Milliseconds (250 * $attempt)
    }
  }
}

function Wait-TalosHealth([int]$TimeoutSeconds) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    foreach ($port in 8787..8806) {
      try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2
        if ($health -and $health.ok -and $health.service -eq $script:appName) {
          return [ordered]@{ port = $port; health = $health }
        }
      } catch {
      }
    }
    Start-Sleep -Milliseconds 500
  }
  throw "Timed out waiting for installed Talos /api/health."
}

function Write-SmokeEvidence([string]$Path, [hashtable]$Evidence) {
  $Evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding utf8
}

$identity = Get-Content -LiteralPath (Join-Path $root "config\app_identity.json") -Raw | ConvertFrom-Json
$script:appName = $identity.display_name
$releaseName = "$appName-$($identity.version)-$(Get-Slug $identity.channel)"
$releaseDir = Join-Path $root "releases\$releaseName"
$installerPath = Join-Path $releaseDir "$releaseName-setup.exe"
$evidencePath = Join-Path $releaseDir "installed_app_smoke.json"

if (-not $SkipBuild) {
  & (Join-Path $root "scripts\build_installer.ps1") -AllowDirty:$AllowDirty
  if ($LASTEXITCODE -ne 0) {
    throw "Installer build failed with exit code $LASTEXITCODE"
  }
}

if (-not (Test-Path -LiteralPath $installerPath)) {
  throw "Installer was not found: $installerPath"
}

$smokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) "TalosInstalledAppSmoke"
$installDir = Join-Path $smokeRoot $appName
$appDataDir = Join-Path $smokeRoot "app-data"
$installedExe = Join-Path $installDir "$appName.exe"
$uninstaller = Join-Path $installDir "unins000.exe"
$oldAppData = $env:TALOS_APP_DATA_DIR
$appProcess = $null
$healthResult = $null

if (Test-Path -LiteralPath $installDir) {
  $oldUninstaller = Join-Path $installDir "unins000.exe"
  if (Test-Path -LiteralPath $oldUninstaller) {
    Invoke-ProcessChecked $oldUninstaller @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") "Previous uninstall"
  }
  if (Test-Path -LiteralPath $installDir) {
    Remove-Item -LiteralPath $installDir -Recurse -Force
  }
}
if (Test-Path -LiteralPath $appDataDir) {
  Remove-Item -LiteralPath $appDataDir -Recurse -Force
}

New-Item -ItemType Directory -Path $smokeRoot -Force | Out-Null
Invoke-ProcessChecked $installerPath @(
  "/VERYSILENT",
  "/SUPPRESSMSGBOXES",
  "/NORESTART",
  "/DIR=$installDir"
) "Silent install"

if (-not (Test-Path -LiteralPath $installedExe)) {
  throw "Installed executable was not found: $installedExe"
}
if (Test-Path -LiteralPath (Join-Path $installDir "config\config.json")) {
  throw "Installed app contains writable config in the install directory."
}

try {
  if (-not $SkipLaunch) {
    $env:TALOS_APP_DATA_DIR = $appDataDir
    $appProcess = Start-Process -FilePath $installedExe -WorkingDirectory $installDir -PassThru
    $healthResult = Wait-TalosHealth $TimeoutSec
    $build = $healthResult.health.build
    if ($build.mode -ne "packaged") {
      throw "Installed app did not report packaged mode."
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$build.root) -and [string]$build.root -like "$root*") {
      throw "Installed app build root points back to the source checkout."
    }
  }

  $manualSteps = @(
    "launch-installed-talos",
    "detect-open-arduino-sketch",
    "select-sketch-and-board",
    "open-source-file",
    "edit-in-talos-and-save-file",
    "verify-sandbox",
    "ask-codex-for-safe-change",
    "review-apply-and-save-codex-change",
    "verify-sandbox-again",
    "confirm-arduino-ide-reflects-saved-change"
  )
  $status = if ($ManualArduinoConfirmed -and (-not $SkipLaunch)) { "passed" } elseif ($ManualArduinoConfirmed) { "manual-confirmed-without-launch-check" } else { "manual-confirmation-required" }
  $evidence = @{
    schema_version = 1
    test = "installed-app-arduino-codex-smoke"
    status = $status
    checked_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")
    release = $releaseName
    installer = $installerPath
    install_dir = $installDir
    app_data_dir = $appDataDir
    automated = @{
      installed_executable = (Test-Path -LiteralPath $installedExe)
      install_dir_config_absent = -not (Test-Path -LiteralPath (Join-Path $installDir "config\config.json"))
      launched = -not $SkipLaunch
      health_port = if ($healthResult) { $healthResult.port } else { $null }
      packaged_mode = if ($healthResult) { $healthResult.health.build.mode -eq "packaged" } else { $false }
    }
    manual = @{
      confirmed = [bool]$ManualArduinoConfirmed
      required_steps = $manualSteps
    }
  }
  Write-SmokeEvidence $evidencePath $evidence

  if (-not $ManualArduinoConfirmed) {
    Write-Warning "Automated installed-app smoke completed, but Arduino/Codex manual confirmation is still required."
    Write-Host "Manual checklist: docs\INSTALLED_APP_SMOKE_TEST.md"
  }
  Write-Host "Installed app smoke evidence:"
  Write-Host $evidencePath
} finally {
  Stop-InstalledTalos $appProcess $installDir
  $env:TALOS_APP_DATA_DIR = $oldAppData
  if (-not $KeepInstalled) {
    if (Test-Path -LiteralPath $uninstaller) {
      Invoke-ProcessChecked $uninstaller @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") "Smoke uninstall"
    }
    if (Test-Path -LiteralPath $installDir) {
      Remove-TreeWithRetry $installDir
    }
    if (Test-Path -LiteralPath $appDataDir) {
      Remove-TreeWithRetry $appDataDir
    }
  }
}
