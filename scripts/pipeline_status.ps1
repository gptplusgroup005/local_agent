param(
    [string]$Path = "docs/TALOS_PIPELINE.md"
)

$ErrorActionPreference = "Stop"
$pipelinePath = Resolve-Path -LiteralPath $Path
$lines = Get-Content -LiteralPath $pipelinePath

$stages = @()
$current = $null

foreach ($line in $lines) {
    if ($line -match '^## Stage\s+(\d+)\s+-\s+(.+)$') {
        $current = [pscustomobject]@{
            Number = [int]$matches[1]
            Name = $matches[2].Trim()
            Done = 0
            Total = 0
            Pending = New-Object System.Collections.Generic.List[string]
        }
        $stages += $current
        continue
    }

    if ($null -eq $current) {
        continue
    }

    if ($line -match '^\s*-\s+\[(x|X| )\]\s+(.+)$') {
        $current.Total++
        if ($matches[1] -match 'x|X') {
            $current.Done++
        } else {
            $current.Pending.Add($matches[2].Trim()) | Out-Null
        }
    }
}

$totalDone = 0
$totalItems = 0

Write-Host "Talos pipeline status"
Write-Host "Source: $pipelinePath"
Write-Host ""

foreach ($stage in $stages) {
    $totalDone += $stage.Done
    $totalItems += $stage.Total
    $percent = if ($stage.Total -gt 0) { [math]::Round(($stage.Done / $stage.Total) * 100) } else { 0 }
    Write-Host ("Stage {0}: {1} - {2}/{3} ({4}%)" -f $stage.Number, $stage.Name, $stage.Done, $stage.Total, $percent)
    if ($stage.Pending.Count -gt 0) {
        Write-Host ("  Next: {0}" -f $stage.Pending[0])
    }
}

Write-Host ""
$overall = if ($totalItems -gt 0) { [math]::Round(($totalDone / $totalItems) * 100) } else { 0 }
Write-Host ("Overall: {0}/{1} ({2}%)" -f $totalDone, $totalItems, $overall)
