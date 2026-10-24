# Run the original Pinterest pipeline on the PC.
# Loads .env into this process, sets PUBLISHER_MODE=local, and schedules
# one channel. Accounts with runner: docker or runner: off are skipped.
# Usage:
#   powershell -File tools/oneoff/run_pinterest_local.ps1
#   powershell -File tools/oneoff/run_pinterest_local.ps1 -Channel ancient_knowledge -Quantity 1 -DryRun

param(
    [string]$Channel = "ancient_knowledge",
    [int]$Quantity = 1,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
Set-Location $root

$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }
        $name, $value = $line.Split("=", 2)
        $name = $name.Trim()
        $value = $value.Trim().Trim('"').Trim("'")
        if ($name) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

$env:PUBLISHER_MODE = "local"
$arguments = @(
    "agents/posting/pinterest_main.py",
    "schedule",
    "--channel", $Channel,
    "--quantity", "$Quantity",
    "--no-wait"
)
if ($DryRun) {
    $arguments += "--dry-run"
}

Write-Host "PUBLISHER_MODE=local channel=$Channel quantity=$Quantity dry_run=$DryRun"
python @arguments
exit $LASTEXITCODE
