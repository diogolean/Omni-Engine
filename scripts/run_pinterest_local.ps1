# Shim until M14. The script lives in tools/oneoff/run_pinterest_local.ps1.
param(
    [string]$Channel = "ancient_knowledge",
    [int]$Quantity = 1,
    [switch]$DryRun
)
$target = Join-Path (Split-Path -Parent $PSScriptRoot) "tools\oneoff\run_pinterest_local.ps1"
& $target -Channel $Channel -Quantity $Quantity -DryRun:$DryRun
exit $LASTEXITCODE
