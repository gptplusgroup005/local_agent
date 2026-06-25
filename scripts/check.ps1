$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir

$ErrorActionPreference = "Stop"
Set-Location $root

Write-Host "== Build native DLL =="
& (Join-Path $root "scripts\build_native.ps1")

Write-Host ""
Write-Host "== Check native exports =="
$nativeCheck = @"
from talos import native_bridge as nb

if not nb.native_available():
    raise SystemExit("native DLL was not loaded")
if not getattr(nb, "_HAS_NATIVE_WINDOW_ROWS", False):
    raise SystemExit("native DLL is missing talos_list_window_rows")
if not getattr(nb, "_HAS_NATIVE_PROCESS_ROWS", False):
    raise SystemExit("native DLL is missing talos_list_arduino_process_rows")

print("native DLL loaded with window/process exports")
"@
$nativeCheck | python -B -
if ($LASTEXITCODE -ne 0) {
  throw "Native export check failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "== Benchmark native detection =="
python -B scripts\benchmark_native.py --iterations 3 --max-native-ms 250
if ($LASTEXITCODE -ne 0) {
  throw "Native benchmark failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "== Run unit tests =="
python -B -m unittest tests.test_desktop_app
if ($LASTEXITCODE -ne 0) {
  throw "Unit tests failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "== Pipeline status =="
& (Join-Path $root "scripts\pipeline_status.ps1")
