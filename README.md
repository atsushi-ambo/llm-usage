# llm-usage

**One place to see all the LLM usage you actually use** — Claude, OpenAI, **Codex (ChatGPT free/plus)**, **Grok Build (X Premium)**, Cursor, and Gemini.

Works from:

- **Local logs** (no API keys): Claude Code, Codex rollouts, Grok Build `unified.jsonl`, Gemini CLI  
- **Live subscription quota**: Codex ChatGPT OAuth (`~/.codex/auth.json`), Grok Build billing snapshots  
- **Provider APIs** when you add keys: Anthropic Admin, OpenAI Org Usage/Costs, Cursor Admin/session, xAI API  

Includes a **CLI** and a **local web dashboard**.

---

## Install (use from anywhere)

One-shot install (puts `llm-usage` on your PATH via `uv tool`, same as other tools in `~/.local/bin`):

```bash
cd llm-usage
./install.sh
```

Or manually:

```bash
uv tool install -e .
```

Then from **any directory**:

```bash
llm-usage status
llm-usage
llm-usage --days 7
llm-usage --format json
llm-usage dashboard   # → http://127.0.0.1:8765
llm-usage menubar     # macOS: quota % in the menu bar (click for all AIs)
```

### macOS menu bar (like Kanary)

Shows compact quotas near the clock, e.g. `C41 · G63 · X29` (Claude / Grok / Codex).

```bash
llm-usage menubar
# start at login:
./scripts/install-menubar-launchagent.sh
```

Click the title → per-app % used, Open Dashboard, Refresh, Quit.

`~/.local/bin` is already on your PATH if you use `uv` / Grok Build. If a new terminal can’t find the command:

```bash
export PATH="$HOME/.local/bin:$PATH"   # add to ~/.zshrc if needed
```

### Update after code changes

The install is editable (`-e`), so most edits under the repo apply immediately. If the CLI entrypoint changes:

```bash
uv tool install --force -e .
# or: ./install.sh
```

### Uninstall

```bash
uv tool uninstall llm-usage
```

Optional: copy env template and fill only the keys you have.

```bash
cp .env.example ~/.config/llm-usage/.env
```

`~/.config/llm-usage/.env` is the only `.env` loaded automatically —
llm-usage does **not** read a `.env` from your current directory, since it's
often run from inside other projects' repos and an untrusted checkout's
`.env` could otherwise override your API keys or the dashboard bind host.
For a project-local `.env` during development, opt in explicitly:

```bash
LLM_USAGE_ENV_FILE=.env llm-usage status
```

---

## What each provider supports

| Provider | Without keys | With keys |
| --- | --- | --- |
| **Claude** | Parses `~/.claude/projects/**/*.jsonl` (Claude Code). Estimates cost from public prices. Optional OAuth quota from `~/.claude/.credentials.json`. | `ANTHROPIC_ADMIN_KEY` → official Usage + Cost Admin API |
| **OpenAI (API)** | — | `OPENAI_ADMIN_KEY` (preferred) or `OPENAI_API_KEY` → org usage/costs |
| **Codex (ChatGPT plan)** | Parses `~/.codex/sessions/**/*.jsonl`. Live free/plus quota from `~/.codex/auth.json` → ChatGPT `/wham/usage`. | No extra key — Free plan works |
| **Grok Build (X Premium)** | Parses `~/.grok/logs/unified.jsonl` (tokens + weekly credit %). Sessions under `~/.grok/sessions/`. | Optional `XAI_API_KEY` for separate pay-as-you-go API |
| **Cursor** | Console link | `CURSOR_API_KEY` or `CURSOR_SESSION_TOKEN` |
| **Gemini** | Parses `~/.gemini/**` CLI chat logs when present | `GEMINI_API_KEY` |

### Codex free plan & Grok Build

These are **subscription quotas**, not dollar invoices:

- **Codex Free**: shows token totals from local sessions + live `% of window used` and `plan_type=free`.
- **Grok Build / X Premium**: shows inference token totals from Grok Build logs + weekly `creditUsagePercent` (e.g. 46% of this week’s included quota).

Costs marked **estimated** (`~`) come from local token counts × public list prices — not invoices.

---

## CLI

```bash
llm-usage                    # summary table (last 30 days)
llm-usage -d 7               # last 7 days
llm-usage -p claude          # one provider
llm-usage -f json            # machine-readable
llm-usage status             # which sources are configured
llm-usage export -o out.json
llm-usage dashboard --port 8765
```

---

## Configuration

Environment variables (also loaded from `.env` or `~/.config/llm-usage/.env`):

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_ADMIN_KEY` | Claude Console admin key (`sk-ant-admin01-…`) |
| `OPENAI_ADMIN_KEY` | OpenAI org admin key (usage/costs) |
| `OPENAI_API_KEY` | Fallback OpenAI key |
| `XAI_API_KEY` | xAI inference key |
| `XAI_MANAGEMENT_KEY` / `XAI_TEAM_ID` | xAI management API |
| `CURSOR_API_KEY` | Cursor Enterprise Admin API key |
| `CURSOR_SESSION_TOKEN` | Browser cookie `WorkosCursorSessionToken` |
| `GEMINI_API_KEY` | Google AI Studio / Generative Language key |
| `LLM_USAGE_DAYS` | Default lookback (30) |
| `LLM_USAGE_PORT` | Dashboard port (8765) |

### Getting Cursor session token (personal plans)

1. Log in at [cursor.com/dashboard](https://cursor.com/dashboard)  
2. DevTools → Application → Cookies → copy `WorkosCursorSessionToken`  
3. Set `CURSOR_SESSION_TOKEN=…` in `.env`  

Enterprise teams should prefer `CURSOR_API_KEY` from Dashboard → API Keys.

### Anthropic Admin key

Claude Console → Settings → Admin API keys. Required for official org usage/cost reports. Individual accounts may not have Admin API access — local Claude Code logs still work.

---

## Architecture

```
src/llm_usage/
  cli.py                 # Typer CLI
  config.py              # env / .env settings
  models.py              # ProviderReport, AggregateReport
  pricing.py             # approximate $/MTok table
  logcache.py            # per-file cache for local log scanning
  serialize.py           # report -> dict, redacting raw upstream payloads
  providers/
    __init__.py          # collect_all / collect_all_cached
    claude.py            # local JSONL + Admin API + OAuth usage
    openai_provider.py   # org usage + costs
    xai.py               # models / management keys
    cursor.py            # admin + dashboard session
    gemini.py            # local CLI logs + models list
  dashboard/
    app.py               # FastAPI
    static/index.html    # single-page UI
  menubar.py              # macOS menu bar (rumps)
```

All collection is **read-only**. No data is uploaded; the dashboard binds to `127.0.0.1` by default.

### Caching

Two independent caches keep repeated invocations cheap and providers'
rate limits happy:

- **Per-file log cache** (`logcache.py`): each local log file (Claude Code
  session, Codex rollout, Grok's unified log, a Gemini CLI chat file) is
  parsed once and cached by an `(mtime, size)` fingerprint under
  `~/.config/llm-usage/cache/logscan/`. Unchanged files are never
  re-parsed, so collection cost stops growing with total history — this is
  what keeps `llm-usage menubar`'s 2-minute poll cheap after months of use.
- **Shared report snapshot** (`collect_all_cached` in `providers/__init__.py`):
  the full collected report is cached on disk for 60 seconds by default, so
  the CLI, dashboard, and menubar landing close together in time reuse one
  collection instead of each independently re-hitting every provider API.
  Pass `--fresh` to `llm-usage`/`show`/`export`, click "Refresh" in the
  dashboard (`?refresh=1`), or click "Refresh Now" in the menubar to bypass
  it.

### Dashboard security

- Each `llm-usage dashboard` run generates a fresh, random token (never
  written to disk in plaintext except a 0600 session file used only so
  `llm-usage menubar`'s "Open Dashboard" can reuse it). The printed URL
  includes `?token=...` — open that link; the browser then holds an
  HttpOnly cookie for the rest of the session.
- The `Host` header is checked against loopback names on every request,
  which blocks DNS-rebinding attempts to read your usage data from a
  malicious webpage.
- `/api/usage` responses and `export` output strip raw upstream payloads
  (full OAuth usage bodies, billing snapshots, API key listings) by default;
  pass `--include-raw` to `export` if you need them for debugging.

---

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

---

## License

MIT
