# Networking Agent

A Claude Code plugin that automates professional networking outreach for aerospace and space-tech job seekers. It discovers relevant contacts at target companies, drafts personalized LinkedIn and cold-email messages using your resume and voice, and walks through an approval loop before writing a final outreach artifact — all from slash commands inside Claude Code.

**Status:** v0.1.0 — pipeline complete, production-ready for personal use.

---

## Requirements

- Python 3.11+
- Claude Code with plugin support (`claude plugin validate` available)
- API keys for Anthropic, Serper, and Hunter (free tiers sufficient for personal use)

---

## Install

```bash
claude plugin install https://github.com/<your-username>/networking-agent
```

**Development / local install:**

```bash
claude --plugin-dir ./networking-agent
```

---

## First Run

```bash
# 1. Verify setup
/network-check

# 2. Run the full pipeline for a company
/network-run spacex
```

`/network-check` will tell you exactly what is missing before you spend any API credits.

---

## Configuration

### 1. Create the config directory and file

```bash
mkdir -p ~/.networking-agent
cp config/default.yaml ~/.networking-agent/config.yaml
```

### 2. Fill in your API keys

```yaml
keys:
  anthropic_api_key: "sk-ant-..."
  serper_api_key: "..."
  hunter_api_key: "..."

providers:
  serper_monthly_limit: 100   # free tier
  hunter_monthly_limit: 25    # free tier

pipeline:
  finder_limit: 5             # contacts discovered per company
```

### 3. Lock down permissions

```bash
chmod 600 ~/.networking-agent/config.yaml
```

**WARNING:** `config.yaml` contains live API keys. It must be `chmod 600` (owner read/write only). `/network-check` will fail if the file is world-readable.

### Environment variable alternative

If you prefer not to write keys to disk, export them before launching Claude Code:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export SERPER_API_KEY="..."
export HUNTER_API_KEY="..."
```

Environment variables take precedence over `config.yaml`.

---

## Voice / Tone Setup

The Drafter agent uses `~/.networking-agent/voice.md` to match your writing style. Without it, drafts will use a neutral default tone.

```bash
cp config/voice.example.md ~/.networking-agent/voice.md
# Then edit voice.md to describe your preferred tone, phrases to avoid, etc.
```

---

## Cost Estimate

All AI calls use `claude-haiku-4-5-20251001`. Per-run cost is approximately **$0.10–0.30 per company**.

| Companies/month | Contacts/company | Est. Claude cost | Serper cost | Hunter cost | Total |
|---|---|---|---|---|---|
| 10 | 5 | ~$0.10 | ~$0.05 | ~$0.10 | ~$0.25 |
| 25 | 5 | ~$0.25 | ~$0.10 | ~$0.25 | ~$0.60 |
| 50 | 5 | ~$0.50 | ~$0.20 | ~$0.50 | ~$1.20 |

See [docs/COSTS.md](docs/COSTS.md) for a detailed per-stage cost breakdown and tips for keeping costs low.

---

## Commands

| Command | Description |
|---|---|
| `/network-check` | Preflight check — verifies API keys, DB integrity, config file permissions, and voice.md presence |
| `/network-run <slug>` | Run the full pipeline (find → select → draft → approve → artifact), or resume from current state |
| `/network-find <slug>` | Discover and score contacts only; stops before drafting |
| `/network-draft <slug>` | Generate draft messages for already-selected contacts |
| `/network-approve <slug>` | Enter the approval loop for drafted messages |
| `/network-status [slug]` | Show pipeline state for one company or all companies |
| `/network-dry-run <slug>` | Simulate a full run without making any API calls or DB writes |
| `/network-purge [slug]` | Delete all stored data for a company (or all companies) — use for GDPR compliance |
| `/network-providers` | Show current API quota usage and remaining credits for each provider |

### Pipeline states

```
NEW → FOUND → SELECTED → DRAFTED → APPROVED → SENT
```

`/network-run` always resumes from the current state, so it is safe to re-run after an interruption.

---

## Troubleshooting

### 1. `ANTHROPIC_API_KEY not set or invalid`

Set the key in `~/.networking-agent/config.yaml` under `keys.anthropic_api_key`, or export `ANTHROPIC_API_KEY` in your shell. Verify the key is active at [console.anthropic.com](https://console.anthropic.com).

### 2. `config.yaml permissions too open (expected 600, got 644)`

Run `chmod 600 ~/.networking-agent/config.yaml`. The plugin refuses to load keys from a world-readable file to prevent accidental credential exposure.

### 3. `Serper quota exhausted` / `Hunter quota exhausted`

Free tiers are limited (Serper: 100 searches/month, Hunter: 25 verifications/month). Use `/network-providers` to check remaining quota. Run `/network-dry-run` to test flows without consuming credits. Upgrade your plan or wait for the monthly reset.

### 4. `voice.md not found`

Copy the example: `cp config/voice.example.md ~/.networking-agent/voice.md`. The pipeline will continue without it, but drafts will use a generic tone rather than your personal voice.

### 5. `DB integrity check failed` or `pipeline state corrupted`

The SQLite database lives at `~/.networking-agent/data.db`. If it is corrupted, delete it — the pipeline will recreate it on next run. Use `/network-purge <slug>` first to cleanly remove specific company data, or delete `data.db` entirely to reset everything.

---

## GDPR / Data Deletion

Contact data (names, emails, LinkedIn URLs) is stored locally in `~/.networking-agent/data.db`. To delete data for a specific company:

```bash
/network-purge spacex
```

To delete all stored data:

```bash
/network-purge
```

This removes all records from the local database. No data is sent to third-party services beyond what is required for contact discovery (Serper search, Hunter email verification).

---

## License

MIT — see [LICENSE](LICENSE).
