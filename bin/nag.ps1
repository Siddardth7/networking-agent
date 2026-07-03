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
# Prefer real interpreters on PATH (`python`/`python3` — what the user/CI
# configured), then fall back to the py launcher with explicit versions. The
# version gate below skips anything < 3.11, so an old `python` is passed over.
# Each candidate is [exe, prefix-args...].
$candidates = @(
    @('python'), @('python3'),
    @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11')
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
    # PowerShell does NOT throw on a native exe's non-zero exit even under
    # ErrorActionPreference=Stop, so every step is checked explicitly.
    & $pyExe @pyArgs '-m' 'venv' $venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPy)) {
        throw "networking-agent: venv creation failed (exit $LASTEXITCODE) at '$venv' using '$pyExe $pyArgs'."
    }
    & $venvPy -m pip install -q --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "networking-agent: pip self-upgrade failed (exit $LASTEXITCODE)." }
    & $venvPy -m pip install -q -r $req
    if ($LASTEXITCODE -ne 0) { throw "networking-agent: dependency install failed (exit $LASTEXITCODE)." }
    Set-Content -LiteralPath $stamp -Value $curHash -NoNewline
}

# --- Run from the source tree (data files resolve package-relative) --------
# Force UTF-8 stdio so the ✓/✗/⚠ status glyphs encode on Windows consoles,
# which default to a legacy codepage (cp1252) and would raise UnicodeEncodeError.
$env:PYTHONUTF8 = '1'
Set-Location $root
& $venvPy -m @args
exit $LASTEXITCODE
