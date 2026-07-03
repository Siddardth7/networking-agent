---
description: "Run preflight validation checks: SQLite version, DB integrity, schema version, config.yaml permissions, Anthropic/Serper/Hunter API key live pings, provider quotas, and voice doc. Prints ✓/✗/⚠ per check. Exit code 1 if any errors."
---

# /network-check


> **Shell note (Windows):** the commands below use the plugin's Python runner. In bash / WSL / Git-Bash use `"${CLAUDE_PLUGIN_ROOT}/bin/nag" …` exactly as written; in **native PowerShell** substitute the runner with `& "$env:CLAUDE_PLUGIN_ROOT\bin\nag.ps1" …` (same module and args).

Run all preflight checks before using the networking agent. Validates your environment end-to-end.

When this command is invoked, execute:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/nag" src.cli.network_check
```

The `nag` runner bootstraps an isolated Python environment on first use, so this
works from any install location with no manual venv setup. `${CLAUDE_PLUGIN_ROOT}`
is set automatically by Claude Code for installed plugins.

## What this checks

1. SQLite version ≥ 3.39
2. DB integrity (`PRAGMA integrity_check`) + WAL mode active
3. Schema version matches latest migration (version 1)
4. `~/.networking-agent/config.yaml` permissions = 0600 (skipped if all keys are env vars)
5. Anthropic API key — live ping to `api.anthropic.com`
6. Serper API key — live ping + remaining quota
7. Hunter API key — live ping + remaining quota (⚠ warning if < 5 searches left)
8. Voice doc at `~/.networking-agent/voice.md` — exists and readable (warning if missing, non-fatal)

## Output format

```
Networking Agent — Setup Check
  ✓ SQLite version 3.39+ (3.42.0)
  ✓ DB integrity: state.db OK (WAL mode active)
  ✓ Schema version: 1 (latest)
  ✓ config.yaml permissions: 0600
  ✓ Anthropic API key: valid (live ping 200)
  ✓ Serper API key: valid (98 / 100 free queries remaining this month)
  ✗ Hunter API key: invalid (HTTP 401) — set HUNTER_API_KEY env var or update config.yaml
  ⚠ Voice doc not found at ~/.networking-agent/voice.md — drafts will use defaults (non-fatal)

1 error, 1 warning. Fix errors before running /network-run.
```

Exit code 0 = all green. Exit code 1 = one or more ✗ errors present.

This command is also run automatically as a preflight by `/network-run`.
