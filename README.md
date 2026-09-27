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
llm-usage menubar     # macOS: one provider's quota % next to the clock
```

### macOS menu bar

macOS only. The status item shows one provider at a time, for example `Grok 64%`, plus a short usage bar. Grok is the default. Pick another from **Show in menu bar**. When every provider is idle the item stays on screen as `AI`, so Refresh Now and the dashboard stay reachable.

The menu lists providers with recent activity:

- **Codex** stays listed while its current window still has usage, until that window resets. The headline is the primary window (a 5-hour window when Codex reports one). A change in the weekly window does not bring a reset primary window back. The row shows percent used, percent remaining, and the local reset time. It does not invent a per-day burn pace.
- **Other providers** drop off 30 minutes after the last observed increase in quota or usage, and come back on a later poll when usage increases again.
- The first reading is only a baseline. Observations are stored in `~/.config/llm-usage/menubar.json` and survive restarts. A provider that reports late shows up late.

Polling runs every 30 seconds and asks only for subscription and credit quotas. It does not scan local logs. Each provider row has a colored bar and its headline percent. Extra windows, the plan name, and a pace estimate (when that provider allows one) are nested under the row. macOS notifications fire when a window crosses 70% and again at 90%.

```bash
llm-usage menubar
# start at login:
./scripts/install-menubar-launchagent.sh
```

Click the title for the provider list, Open Dashboard, Refresh Now, and Quit.

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
| **OpenRouter** | — | `OPENROUTER_API_KEY` → pay-as-you-go credit/spend |

Cohere, Mistral, Replicate, and Hugging Face are recognized when `COHERE_API_KEY`, `MISTRAL_API_KEY`, `REPLICATE_API_KEY`, or `HUGGINGFACE_API_KEY` is set. None of them has a usage API wired up, so the report only points at that vendor's billing page.

### Codex free plan & Grok Build

These are **subscription quotas**, not dollar invoices:

- **Codex Free**: token totals from local sessions, plus live quota from ChatGPT. The primary window (often 5 hours) is the headline. The weekly window is listed beside it, with percent used, percent remaining, and when it resets. `plan_type` comes from the same response (for example `free`). The menu bar does not project a burn pace for Codex.
- **Grok Build / X Premium**: shows inference token totals from Grok Build logs + weekly `creditUsagePercent` (e.g. 46% of this week’s included quota).

Costs marked **estimated** (`~`) come from local token counts × public list prices — not invoices. The CLI and dashboard footnote the pricing-table snapshot date (`PRICES_AS_OF` in `pricing.py`).

---

## CLI

```bash
llm-usage                    # summary table (last 30 days)
llm-usage -d 7               # last 7 days
llm-usage -p claude          # one provider
llm-usage -f json            # machine-readable
llm-usage --fresh            # bypass the shared snapshot cache
llm-usage status             # which sources are configured
llm-usage history -w 8       # weekly trend table + sparkline per provider
llm-usage export -o out.json
llm-usage dashboard --port 8765
llm-usage check --format json   # cron: exit 1 if any quota ≥ --fail-at
llm-usage doctor --format json  # live health of every configured source
llm-usage setup                 # interactive configuration wizard
llm-usage validate              # config errors and warnings
llm-usage profile list          # named env profiles (create, switch, delete)
llm-usage schedule list         # saved export jobs (create, run, delete)
```

---

## Configuration

Environment variables, loaded from `~/.config/llm-usage/.env` (or the active profile's env file). A `.env` in the current directory is ignored unless you set `LLM_USAGE_ENV_FILE`:

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
| `OPENROUTER_API_KEY` | openrouter.ai/keys — pay-as-you-go credit/spend |
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
  config.py              # env / profile settings
  models.py              # ProviderReport, AggregateReport
  pricing.py             # approximate $/MTok table
  burnrate.py            # pace projection, or remaining % when pace is off
  quota.py               # quota windows, dashboard session, JSON cache
  logcache.py            # per-file cache for local log scanning
  serialize.py           # report -> dict, redacting raw upstream payloads
  providers/
    __init__.py          # collect_all / collect_all_cached
    claude.py            # local JSONL + Admin API + OAuth usage
    openai_provider.py   # org usage + costs
    codex.py             # local sessions + ChatGPT wham/usage quota
    xai.py               # Grok Build logs + management keys
    cursor.py            # admin + dashboard session
    gemini.py            # local CLI logs + models list
    openrouter.py        # pay-as-you-go credit/spend
    plugin.py            # ~/.config/llm-usage/plugins
  dashboard/
    app.py               # FastAPI
    static/index.html    # page shell
    static/app.js        # dashboard UI
  menubar.py             # macOS menu bar (rumps + AppKit)
  menubar_core.py        # which provider to show, bars, activity
```

All collection is **read-only**. No data is uploaded; the dashboard binds to `127.0.0.1` by default.

### Caching

Two independent caches keep repeated invocations cheap and providers'
rate limits happy:

- **Per-file log cache** (`logcache.py`): each local log file (Claude Code
  session, Codex rollout, Grok's unified log, a Gemini CLI chat file) is
  parsed once and cached by an `(mtime, size)` fingerprint under
  `~/.config/llm-usage/cache/logscan/`. Unchanged files are never
  re-parsed, so a full CLI or dashboard collection stops growing with total
  history. The menu bar does not use this cache: it skips log scans.
- **Shared report snapshot** (`collect_all_cached` in `providers/__init__.py`):
  a collected report is cached on disk for 90 seconds by default, so a CLI
  run and a dashboard load in that window share one collection. The menu bar
  keeps its own quota-only snapshot for 20 seconds, keyed separately so it
  never collides with a full report. Pass `--fresh` to `llm-usage`/`show`/`export`,
  click "Refresh" in the dashboard (`?refresh=1`), or click "Refresh Now" in
  the menu bar to bypass the snapshot.

### Dashboard security

- Binds to **loopback only** by default (`127.0.0.1`). A non-local bind
  requires an explicit `--i-understand-no-auth` flag (token-only protection
  is not enough on untrusted networks).
- Each `llm-usage dashboard` run generates a fresh random token. The printed
  URL includes `?token=...`; the browser then holds an HttpOnly cookie.
  The same token is also stored mode `0600` under
  `~/.config/llm-usage/cache/` so the menubar can open an authenticated tab.
- The `Host` header is checked against loopback names on every request,
  which blocks DNS-rebinding attempts to read your usage data from a
  malicious webpage.
- `/api/usage` responses and `export` output strip raw upstream payloads
  (full OAuth usage bodies, billing snapshots, API key listings) by default;
  pass `--include-raw` to `export` if you need them for debugging.
- Secrets live only in `~/.config/llm-usage/.env` (or env vars) and in other
  tools' credential stores (`~/.claude`, `~/.codex`, `~/.grok`, Keychain).
  Nothing secret is committed to this repository.

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
