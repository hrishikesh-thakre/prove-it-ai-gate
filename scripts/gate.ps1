# gate.ps1 — prove-it-ai-gate daily workflow wrapper (PowerShell)
param(
    [Parameter(Mandatory=$true)]
    [string]$Brief,

    [string]$Evidence = "./evidence",
    [string]$WikiPath = "",
    [string]$Learnings = "./learnings",
    [string]$Transcript = "./transcript.jsonl",
    [string]$TaskType = "audit"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Brief)) {
    Write-Error "Brief not found: $Brief"
    exit 1
}

if (-not (Test-Path -LiteralPath $Evidence)) {
    Write-Error "Evidence folder not found: $Evidence"
    Write-Output "Create an evidence/ folder with at minimum: brief.md, closeout.md,"
    Write-Output "  workspace/git_status_before.txt, workspace/git_status_after.txt"
    exit 1
}

if (-not (Test-Path -LiteralPath $Transcript)) {
    Write-Error "Transcript not found: $Transcript"
    Write-Output "Save your agent transcript as transcript.jsonl before running the gate."
    exit 1
}

if ($WikiPath -and (Test-Path -LiteralPath $WikiPath)) {
    Write-Output "=== Scout: reuse scan ==="
    try {
        ai-gate reuse-scan --brief $Brief --local-wiki $WikiPath --output $Learnings
    } catch {
        Write-Output "Scout failed (non-blocking): $_"
    }
} else {
    Write-Output "=== Scout: skipped (no WIKI_PATH or wiki not found) ==="
}

Write-Output "=== Gate: acceptance check ==="
ai-gate accept --repo . --evidence $Evidence --transcript $Transcript --task-type $TaskType

$latestReport = Get-ChildItem -Path "." -Filter "acceptance_report_*.json" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if ($latestReport) {
    New-Item -ItemType Directory -Force -Path $Learnings | Out-Null
    Write-Output "=== Capture: reusable learnings ==="
    ai-gate capture --report-json $latestReport.FullName --evidence $Evidence --output $Learnings
} else {
    Write-Output "=== Capture: skipped (no acceptance report found) ==="
}

Write-Output "=== Done ==="
if ($latestReport) { Write-Output "Report: $($latestReport.Name)" }
Write-Output "Learnings: $Learnings"
