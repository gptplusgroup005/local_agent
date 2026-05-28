$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
  $fallback = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
  if (Test-Path -LiteralPath $fallback) {
    $ollama = @{ Source = $fallback }
  }
}

if (-not $ollama) {
  Write-Host "Ollama is not installed or not in PATH."
  Write-Host "Install Ollama first, then run this script again."
  exit 1
}

$configPath = Join-Path $root "config.json"
$config = if (Test-Path -LiteralPath $configPath) {
  Get-Content $configPath -Raw | ConvertFrom-Json
} else {
  [pscustomobject]@{
    model = "qwen3:8b"
    model_enabled = $false
  }
}
if (-not $config.PSObject.Properties["model"]) {
  $config | Add-Member -NotePropertyName model -NotePropertyValue "qwen3:8b"
}
if (-not $config.PSObject.Properties["model_enabled"]) {
  $config | Add-Member -NotePropertyName model_enabled -NotePropertyValue $false
}
$model = if ($config.model) { $config.model } else { "qwen3:8b" }
& $ollama.Source pull $model

$config.model = $model
$config.model_enabled = $true
$config | ConvertTo-Json -Depth 8 | Set-Content $configPath -Encoding UTF8

Write-Host "Model is ready: $model"
