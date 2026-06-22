param(
    [switch]$KeepStaging
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$targets = @((Join-Path $root ".talos_sandbox"))
if (-not $KeepStaging) {
    $targets += Join-Path $root ".talos_staging"
}
$targets += Get-ChildItem -LiteralPath $root -Directory -Force -Filter "pytest-cache-files-*" |
    ForEach-Object { $_.FullName }
$targets += Get-ChildItem -LiteralPath $root -Directory -Force -Recurse -Filter "__pycache__" |
    ForEach-Object { $_.FullName }

foreach ($candidate in $targets | Select-Object -Unique) {
    $target = Resolve-Path -LiteralPath $candidate -ErrorAction SilentlyContinue
    if ($null -eq $target) {
        continue
    }
    if (-not $target.Path.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside the Talos workspace: $($target.Path)"
    }
    try {
        Remove-Item -LiteralPath $target.Path -Recurse -Force
        Write-Host "Removed $($target.Path)"
    } catch {
        Write-Warning "Could not remove $($target.Path) because it is in use or protected: $($_.Exception.Message)"
    }
}
