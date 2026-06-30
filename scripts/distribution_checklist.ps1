param(
  [string]$ReleaseDir = "",
  [string]$OutputPath = "",
  [switch]$RequireReady
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
Set-Location $root
$ErrorActionPreference = "Stop"

function Get-Slug([string]$value) {
  return ($value.Trim().ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
}

function Read-JsonFile([string]$Path) {
  if (Test-Path -LiteralPath $Path) {
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
  }
  return $null
}

function Escape-Markdown([string]$Value) {
  if ($null -eq $Value) {
    return ""
  }
  return ($Value -replace '\|', '\|') -replace "`r?`n", "<br>"
}

function Resolve-ProjectPath([string]$Path) {
  if ([System.IO.Path]::IsPathRooted($Path)) {
    return [System.IO.Path]::GetFullPath($Path)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $root $Path))
}

function Get-KnownLimitations {
  $notesPath = Join-Path $root "docs\RELEASE_NOTES.md"
  if (-not (Test-Path -LiteralPath $notesPath)) {
    return @("- Release notes were not found.")
  }

  $lines = Get-Content -LiteralPath $notesPath
  $items = New-Object System.Collections.Generic.List[string]
  $inside = $false
  foreach ($line in $lines) {
    if ($line -match '^###\s+Known Limitations') {
      $inside = $true
      continue
    }
    if ($inside -and $line -match '^###\s+') {
      break
    }
    if ($inside -and $line.Trim().StartsWith("- ")) {
      $items.Add($line.Trim())
    }
  }
  if ($items.Count -eq 0) {
    return @("- No known limitations were listed in release notes.")
  }
  return @($items)
}

$identity = Read-JsonFile (Join-Path $root "config\app_identity.json")
if (-not $identity) {
  throw "config\app_identity.json was not found."
}

$releaseName = "$($identity.display_name)-$($identity.version)-$(Get-Slug $identity.channel)"
if ([string]::IsNullOrWhiteSpace($ReleaseDir)) {
  $ReleaseDir = Join-Path $root "releases\$releaseName"
}
$releaseDirPath = Resolve-ProjectPath $ReleaseDir

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
  $OutputPath = Join-Path $releaseDirPath "DISTRIBUTION_CHECKLIST.md"
}
$outputPathFull = Resolve-ProjectPath $OutputPath

$manifestPath = Join-Path $releaseDirPath "release_manifest.json"
$signingPath = Join-Path $releaseDirPath "signing_status.json"
$installerSmokePath = Join-Path $releaseDirPath "installer_smoke.json"
$installedAppSmokePath = Join-Path $releaseDirPath "installed_app_smoke.json"

$manifest = Read-JsonFile $manifestPath
$signing = Read-JsonFile $signingPath
$installerSmoke = Read-JsonFile $installerSmokePath
$installedAppSmoke = Read-JsonFile $installedAppSmokePath

$artifacts = @()
if ($manifest -and $manifest.artifacts) {
  $artifacts = @($manifest.artifacts)
}

$hasExecutable = @($artifacts | Where-Object { $_.type -eq "windows-executable" }).Count -gt 0
$hasInstaller = @($artifacts | Where-Object { $_.type -eq "windows-installer" }).Count -gt 0
$signingReady = $false
if ($signing) {
  $signingReady = [bool]$signing.signed -or [string]$signing.status -eq "signed" -or [string]$signing.status -eq "unsigned-beta"
}
$installerSmokeReady = $installerSmoke -and [string]$installerSmoke.status -eq "passed"
$installedAppSmokeReady = $installedAppSmoke -and ([string]$installedAppSmoke.status -eq "passed" -or [string]$installedAppSmoke.status -eq "manual-confirmed-without-launch-check")
$requiredReady = $manifest -and $hasExecutable -and $hasInstaller -and $signingReady -and $installerSmokeReady -and $installedAppSmokeReady

if ($RequireReady -and -not $requiredReady) {
  throw "Distribution checklist is not release-ready. Build artifacts, signing status, installer smoke, and installed-app smoke must all be present and passing."
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("# Talos Distribution Checklist")
$lines.Add("")
$lines.Add(("Release: ``{0}``" -f $releaseName))
$lines.Add("Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')")
$lines.Add(("Release folder: ``{0}``" -f $releaseDirPath))
$lines.Add("")
$lines.Add("## Release Gate")
$lines.Add("")
$lines.Add("| Gate | Status | Evidence |")
$lines.Add("| --- | --- | --- |")
$lines.Add(("| Release manifest | {0} | ``release_manifest.json`` |" -f $(if ($manifest) { "present" } else { "missing" })))
$lines.Add(("| Standalone executable artifact | {0} | ``release_manifest.json`` artifacts |" -f $(if ($hasExecutable) { "present" } else { "missing" })))
$lines.Add(("| Windows installer artifact | {0} | ``release_manifest.json`` artifacts |" -f $(if ($hasInstaller) { "present" } else { "missing" })))
$lines.Add(("| Signing or explicit unsigned Beta | {0} | ``signing_status.json`` |" -f $(if ($signingReady) { "ready" } else { "missing" })))
$lines.Add(("| Installer install/uninstall smoke | {0} | ``installer_smoke.json`` |" -f $(if ($installerSmokeReady) { "passed" } else { "missing" })))
$lines.Add(("| Installed app Arduino/Codex smoke | {0} | ``installed_app_smoke.json`` |" -f $(if ($installedAppSmokeReady) { "passed" } else { "missing/manual" })))
$lines.Add("")
$lines.Add("## Artifacts")
$lines.Add("")
if ($artifacts.Count -gt 0) {
  $lines.Add("| File | Type | Bytes | SHA-256 |")
  $lines.Add("| --- | --- | ---: | --- |")
  foreach ($artifact in $artifacts) {
    $lines.Add(("| {0} | {1} | {2} | ``{3}`` |" -f (Escape-Markdown $artifact.file), (Escape-Markdown $artifact.type), $artifact.bytes, $artifact.sha256))
  }
} else {
  $lines.Add("- No artifacts were found. Run `scripts\build_installer.ps1` first.")
}
$lines.Add("")
$lines.Add("## Installer And Uninstall")
$lines.Add("")
if ($installerSmoke) {
  $lines.Add(("- Status: ``{0}``" -f $installerSmoke.status))
  $lines.Add(("- Install directory: ``{0}``" -f $installerSmoke.install_dir))
  $lines.Add(("- Start Menu shortcut checked: ``{0}``" -f $installerSmoke.start_menu_shortcut))
  $lines.Add(("- Uninstall cleanup checked: ``{0}``" -f $installerSmoke.uninstall_cleanup))
} else {
  $lines.Add("- Installer smoke result missing. Run `scripts\smoke_installer.ps1 -SkipBuild` after building the installer.")
}
$lines.Add("")
$lines.Add("## Installed App Smoke")
$lines.Add("")
if ($installedAppSmoke) {
  $lines.Add(("- Status: ``{0}``" -f $installedAppSmoke.status))
  $lines.Add(("- Health launch checked: ``{0}``" -f $installedAppSmoke.automated.launched))
  $lines.Add(("- Packaged mode checked: ``{0}``" -f $installedAppSmoke.automated.packaged_mode))
  $lines.Add(("- Manual Arduino/Codex confirmation: ``{0}``" -f $installedAppSmoke.manual.confirmed))
} else {
  $lines.Add("- Installed-app smoke result missing. Run `scripts\smoke_installed_app.ps1 -SkipBuild -ManualArduinoConfirmed` after manual Arduino/Codex validation.")
}
$lines.Add("")
$lines.Add("## Rollback And Recovery")
$lines.Add("")
$lines.Add("- Stage 9 release recovery smoke is required by `scripts\check.ps1`.")
$lines.Add("- Runtime rollback behavior is covered by checkpoint tests and the release recovery smoke.")
$lines.Add("- Installer rollback/uninstall behavior is covered by `installer_smoke.json` when present.")
$lines.Add("")
$lines.Add("## Known Limitations")
$lines.Add("")
foreach ($item in Get-KnownLimitations) {
  $lines.Add($item)
}
$lines.Add("")
$lines.Add("## Distribution Decision")
$lines.Add("")
$lines.Add("- [ ] Artifact names and hashes reviewed.")
$lines.Add("- [ ] Signing status reviewed.")
$lines.Add("- [ ] Installer install/uninstall evidence reviewed.")
$lines.Add("- [ ] Installed app smoke evidence reviewed.")
$lines.Add("- [ ] Known limitations accepted for this Beta channel.")
$lines.Add("- [ ] Release approved for distribution.")

$outputDir = Split-Path -Parent $outputPathFull
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
$lines | Set-Content -LiteralPath $outputPathFull -Encoding utf8

Write-Host "Distribution checklist written:"
Write-Host $outputPathFull
if (-not $requiredReady) {
  Write-Warning "Checklist was generated, but release-ready evidence is incomplete."
}
