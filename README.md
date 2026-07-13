# llm-usage

**One place to see all the LLM usage you actually use** — Claude, OpenAI, Grok (xAI), Cursor, and Gemini.

Works from:

- **Local logs** (no API keys): Claude Code session JSONL, Gemini CLI chats  
- **Provider APIs** when you add keys: Anthropic Admin, OpenAI Org Usage/Costs, Cursor Admin/session, xAI, Gemini  

Includes a **CLI** and a **local web dashboard**.

---

## Quick start

```bash
cd ~/personal/tool/llm-usage
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# See what's available on this machine (no keys required)
llm-usage status

# Pull usage (uses Claude Code local logs out of the box if present)
llm-usage
llm-usage --days 7
llm-usage --format json

# Local web UI
llm-usage dashboard
# → http://127.0.0.1:8765
```

Optional: copy env template and fill only the keys you have.

```bash
cp .env.example .env
# or: mkdir -p ~/.config/llm-usage && cp .env.example ~/.config/llm-usage/.env
```

---

## What each provider supports

| Provider | Without keys | With keys |
| --- | --- | --- |
| **Claude** | Parses `~/.claude/projects/**/*.jsonl` (Claude Code). Estimates cost from public prices. Optional OAuth quota from `~/.claude/.credentials.json`. | `ANTHROPIC_ADMIN_KEY` → official Usage + Cost Admin API |
| **OpenAI** | — | `OPENAI_ADMIN_KEY` (preferred) or `OPENAI_API_KEY` → `/v1/organization/usage/completions` + `/costs` |
| **Grok (xAI)** | Console links | `XAI_API_KEY` validates models; `XAI_MANAGEMENT_KEY` + `XAI_TEAM_ID` lists team keys. Full spend charts remain in [console.x.ai](https://console.x.ai/team/default/usage) (no public usage time-series API yet) |
| **Cursor** | Console link | `CURSOR_API_KEY` (Enterprise Admin API) or `CURSOR_SESSION_TOKEN` (`WorkosCursorSessionToken` cookie) |
| **Gemini** | Parses `~/.gemini/**` CLI chat logs when present | `GEMINI_API_KEY` validates models; spend in [AI Studio Usage](https://aistudio.google.com/usage) |

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
  providers/
    claude.py            # local JSONL + Admin API + OAuth usage
    openai_provider.py   # org usage + costs
    xai.py               # models / management keys
    cursor.py            # admin + dashboard session
    gemini.py            # local CLI logs + models list
  dashboard/
    app.py               # FastAPI
    static/index.html    # single-page UI
```

All collection is **read-only**. No data is uploaded; the dashboard binds to `127.0.0.1` by default.

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
