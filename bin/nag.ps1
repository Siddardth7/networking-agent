#!/usr/bin/env pwsh
# networking-agent runner (Windows / PowerShell).
#
# PowerShell twin of bin/nag. Makes the plugin portable on Windows: bootstraps
# an isolated venv on first use, then runs `python -m <module> [args...]` from
# the plugin's own source tree. Works from any install location (Claude Code
# plugin cache or a dev clone), with no pre-existing venv and no hardcoded paths.
#
# Usage:  pwsh bin/nag.ps1 src.cli.network_check
#         '<payload>' | pwsh bin/nag.ps1 src.cli.network_classify_host ingest <slug>
$ErrorActionPreference = 'Stop'

# --- Locate the plugin/repo root -------------------------------------------
# Prefer the harness-provided var; fall back to this script's own location
# ($root\bin\nag.ps1) so a plain dev clone works too.
$root = $env:CLAUDE_PLUGIN_ROOT
if (-not $root -or -not (Test-Path (Join-Path $root 'requirements.txt'))) {
    $root = Split-Path -Parent $PSScriptRoot
}
$req = Join-Path $root 'requirements.txt'

# --- Pick a Python 3.11+ (required, see pyproject.toml) ---------------------
# Try the py launcher (standard on Windows) with explicit versions first, then
# bare python/python3. Each candidate is [exe, prefix-args...].
$candidates = @(
    @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'),
    @('python'), @('python3')
)
$verCheck = 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
$pyExe = $null
$pyArgs = @()
foreach ($c in $candidates) {
    $exe = $c[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $pre = if ($c.Count -gt 1) { @($c[1..($c.Count - 1)]) } else { @() }
    & $exe @pre '-c' $verCheck *> $null
    if ($LASTEXITCODE -eq 0) { $pyExe = $exe; $pyArgs = $pre; break }
}
if (-not $pyExe) {
    [Console]::Error.WriteLine(
        "networking-agent: Python 3.11+ required but none found on PATH.`n" +
        "  Install from https://www.python.org/downloads/ (check 'Add python.exe to PATH') and retry.")
    exit 1
}

# --- Bootstrap the isolated venv (idempotent) ------------------------------
$venv    = Join-Path $env:USERPROFILE '.networking-agent\.venv'
$venvPy  = Join-Path $venv 'Scripts\python.exe'
$stamp   = Join-Path $venv '.requirements.sha256'
$curHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $req).Hash

$needBootstrap = $true
if (Test-Path $venvPy) {
    $prev = if (Test-Path $stamp) { (Get-Content -Raw -LiteralPath $stamp).Trim() } else { '' }
    if ($prev -eq $curHash) { $needBootstrap = $false }
}

if ($needBootstrap) {
    [Console]::Error.WriteLine('networking-agent: preparing environment (first run or deps changed)...')
    & $pyExe @pyArgs '-m' 'venv' $venv
    & $venvPy -m pip install -q --upgrade pip
    & $venvPy -m pip install -q -r $req
    Set-Content -LiteralPath $stamp -Value $curHash -NoNewline
}

# --- Run from the source tree (data files resolve package-relative) --------
Set-Location $root
& $venvPy -m @args
exit $LASTEXITCODE
