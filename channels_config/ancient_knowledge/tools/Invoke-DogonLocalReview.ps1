#requires -Version 5.1
<#
.SYNOPSIS
  One Ancient Knowledge Dogon reel, with distribution forced off.

.DESCRIPTION
  Calls production.pipeline.local_review, which passes --local-review to the
  pipeline. That flag refuses YouTube, B2, ImgBB, Post Planner, and library
  writes even when ENABLE_YOUTUBE_UPLOAD is set. --dry-run is not used: it
  applies to ECONOMIC_REEL_LOFI only.

  Output is a new directory under the existing OUTPUT_PATH:
  <OUTPUT_PATH>/ancient_knowledge/clips/local_review/<stamp>/
  The original mp4 named in the QA json is passed as --protect-path.

  This script does not write an approval file.

.PARAMETER QaJson
  Path to the_sirius_mystery_dogon_4ed43c QA json. Its video_path must be
  reel_did_an_african_tribe_map_an_invi_v01.mp4.

.PARAMETER Mode
  render (default) or sample.

.PARAMETER Video
  Required for -Mode sample. The mp4 to sample. The manifest is a new file
  under local_review_samples next to that mp4.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$QaJson,
    [ValidateSet("render", "sample")]
    [string]$Mode = "render",
    [string]$Video
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
Set-Location -LiteralPath $repo

$venv = Join-Path $repo ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venv) {
    $python = $venv
} else {
    $python = "python"
}

if ($Mode -eq "sample") {
    if (-not $Video) {
        throw "-Video is required when -Mode sample"
    }
    & $python -m production.pipeline.local_review sample --video $Video
    exit $LASTEXITCODE
}

& $python -m production.pipeline.local_review render --qa $QaJson
exit $LASTEXITCODE
