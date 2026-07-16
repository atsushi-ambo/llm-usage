# llm-usage — Architecture & Security Review

Date: 2026-07-16 · Scope: full repo at commit `90ca566`
Purpose: actionable findings + improvement plan, written so each item can be handed to an implementation agent as a standalone task.

---

## 1. Current architecture (summary)

```
CLI (typer) ─┐
Dashboard ───┼──▶ collect_all(settings, days) ──▶ [6 provider collectors, sequential]
Menubar ─────┘         │                              │
                       ▼                              ▼
              AggregateReport (pydantic)     local JSONL scans + HTTPS calls
                                             (~/.claude, ~/.codex, ~/.grok, ~/.gemini)
```

- Three frontends (CLI, FastAPI dashboard, macOS menubar) all call the same
  `collect_all()` synchronously.
- Each collector reads local logs and/or calls provider APIs, returning a
  `ProviderReport`. Only the Claude OAuth quota endpoint has caching/backoff
  (`quota.py`); everything else re-fetches and re-scans on every invocation.
- Secrets come from env / `.env` (pydantic-settings) plus tokens read from
  other tools' credential files (`~/.claude/.credentials.json`,
  `~/.codex/auth.json`).

**Strengths worth keeping:** clean provider separation, pydantic models as the
single report schema, read-only collection, localhost-default dashboard with a
warning on non-local bind, 0600/0700 perms on cache/config, the 429
cooldown+stale-cache pattern in `quota.py`, `.env` in `.gitignore`.

---

## 2. Security findings

### S1 (High) — Dashboard renders API data with `innerHTML` without escaping → XSS
`dashboard/static/index.html` builds all cards via template strings into
`innerHTML` (`render()`, `renderQuotaBlock()`, and the error path at line 416).
Injected values include model names, notes, error strings, plan labels, and
`meta.console_url` (into an `href`). Model names and plan strings originate
from **local JSONL logs and remote API responses** — semi-trusted input. A
crafted `model` field in any `~/.claude/projects/**/*.jsonl` or Codex/Grok log
line (e.g. written by a tool you ran, or a malicious repo's hooks) becomes
script running in the dashboard origin. The error path is worse: it injects
the raw HTTP response body (`e.message` ← `await res.text()`), and FastAPI 422
validation errors echo the offending input back verbatim.

**Fix:** escape every interpolated value (single `esc()` helper or build nodes
with `textContent`); validate `console_url` against an allow-list of known
console hosts before putting it in `href`; add a strict CSP header
(`default-src 'self'`) from FastAPI; never `innerHTML` a server error body.

### S2 (High) — No auth / no Host validation on the dashboard API → DNS-rebinding readable secrets
`app.py` has no authentication and no `Host` header check. `/api/usage`
returns highly sensitive data: full OAuth usage payloads
(`meta["subscription"]`), plan/tier info, Cursor spend objects
(`meta["spend"]`), and xAI API-key metadata (`meta["api_keys"]`). Binding to
127.0.0.1 does **not** stop DNS rebinding: a malicious website can point its
own hostname at 127.0.0.1 and read the JSON (same-origin from the browser's
view). Any local process can also read it, and any webpage can *trigger*
collection (see S5).

**Fix (layered):**
1. Add `TrustedHostMiddleware` allowing only `127.0.0.1`, `localhost`, `[::1]`
   (with port variants). This kills DNS rebinding cheaply.
2. Generate a random token on `llm-usage dashboard` start, print the URL as
   `http://127.0.0.1:8765/?token=…`, require it (cookie or header) for
   `/api/*`. Keeps other local users/processes out.
3. Refuse to start on a non-loopback host unless `--i-understand-no-auth`
   (today it only warns).

### S3 (Medium) — API/export responses leak raw provider payloads
`ProviderReport.meta` is a dumping ground for entire upstream response bodies
(`subscription`, `spend`, `raw_dashboard`, `api_keys`). These flow to:
`/api/usage` JSON, `llm-usage --format json`, and `llm-usage export` (written
to disk with default perms, in whatever CWD).

**Fix:** define typed, minimal shapes for what the UI actually needs
(`QuotaInfo` model instead of free-form dict); drop raw bodies or gate them
behind `--debug`; `chmod 0600` the export file and default it into
`~/.config/llm-usage/exports/`.

### S4 (Medium) — `.env` auto-loaded from CWD
`Settings` loads `.env` from the current directory first. Running `llm-usage`
inside an untrusted checkout lets that repo's `.env` override
`LLM_USAGE_HOST` (bind 0.0.0.0), point `*_DIR` at attacker-chosen paths, or
substitute attacker API keys so your usage data is sent to their account.

**Fix:** load only `~/.config/llm-usage/.env` by default; support project
`.env` only via explicit `--env-file` flag. At minimum, ignore
`LLM_USAGE_HOST` from CWD `.env`.

### S5 (Medium) — Unauthenticated endpoint triggers outbound API calls on demand
Every `GET /api/usage` runs the full collection: OAuth calls to Anthropic,
`chatgpt.com/backend-api/wham/usage`, Cursor, xAI. Any local process or
webpage (`fetch` to localhost fires even if the response is unreadable) can
hammer it → rate-limit lockouts (429) on your real accounts, or account
flagging on the unofficial ChatGPT endpoint.

**Fix:** server-side TTL cache of the last report (e.g. 60–120 s; see A1),
plus the auth token from S2. Add `?refresh=1` (auth-required) to bypass.

### S6 (Low) — Credential-file reads and token flows
Reading `~/.claude/.credentials.json` and `~/.codex/auth.json` is the
product's core trick and is acceptable for a local tool, but:
- Tokens are held in memory and never logged — good. Keep it that way; add a
  lint/test asserting tokens never enter `notes`, `errors`, or `meta`.
- Exception messages appended to `report.errors` may embed response bodies
  (`openai_provider.py` truncates to 200 chars — do the same everywhere;
  httpx exceptions can include URLs with query strings).
- `write_json_cache` writes the file then chmods (brief 0644 window). Use
  `os.open(..., O_WRONLY|O_CREAT, 0o600)` or write to a temp file with 0600
  and rename.
- Document clearly in README that the tool reads other apps' credential files
  and exactly which hosts each token is sent to (it currently only ever sends
  each token to its own provider — preserve that invariant with a comment/test).

### S7 (Low) — Supply chain / hygiene
- No dependency pinning (`>=` ranges only) and no lockfile committed. Add
  `uv.lock` and CI that installs from it.
- No CI at all: add GitHub Actions running `ruff`, `pytest`, and `pip-audit`
  (or `uv audit`); enable Dependabot.
- Add a `SECURITY.md` (how to report) — cheap and signals intent.
- `install.sh` and the LaunchAgent script are fine (no curl-pipe-sh, no sudo).

---

## 3. Architecture improvements

### A1 (High impact) — Introduce a snapshot cache shared by CLI / dashboard / menubar
Today the menubar re-runs the entire collection every 120 s, and each
dashboard refresh re-scans **all** JSONL logs from scratch (cost grows
unboundedly with log history) and re-hits every API. Generalize the pattern
already in `quota.py`:

- A `SnapshotStore` (JSON or SQLite under `~/.config/llm-usage/cache/`) that
  stores the last `AggregateReport` with a timestamp; all three frontends read
  it if fresh (TTL configurable, default ~90 s), else collect and update.
- Per-provider TTLs + the existing 429 cooldown mechanism moved into a shared
  helper so Codex/Cursor/xAI get the same backoff behavior Claude has.

### A2 (High impact) — Incremental log scanning
`claude.py`/`codex.py` re-read every line of every JSONL file each run. Keep a
small index (`{path: {mtime, size, byte_offset, partial_totals}}`) so
unchanged files are skipped and appended files are read from the last offset.
This is what makes the menubar's 2-minute cadence sustainable after months of
Claude Code history. Store per-day aggregates in SQLite to also enable
history/trends (see F1).

### A3 — Formalize the provider interface
Collectors share a lot of copy-pasted scaffolding (report creation, jsonl
walking, by_model/by_day accumulation, note/error conventions). Define:

```python
class Provider(Protocol):
    id: ProviderId
    def collect(self, settings, start, end) -> ProviderReport: ...
```

plus a shared `JsonlAccumulator` for the by_model/by_day/totals bookkeeping
(3 near-identical implementations exist in claude.py, codex.py, xai.py).
Register providers in a list/entry-points so adding one (Copilot, OpenRouter,
Windsurf…) is a single file. Run collectors concurrently
(`ThreadPoolExecutor` is enough; httpx calls dominate wall time).

### A4 — Type the `meta` dict
`meta` is stringly-typed and each frontend re-implements quota extraction
(`_quota_of` in menubar, `extractQuota` in JS, notes parsing in CLI). Promote
first-class fields on `ProviderReport`: `quota: QuotaInfo | None`,
`plan: str | None`, `console_url: HttpUrl | None`. Keep `meta` only for debug.

### A5 — Remove the env-var side channels
`cli.py:dashboard_cmd` passes `--days` by mutating `os.environ` before
`uvicorn.run`. Instead, build the FastAPI app via a factory
(`create_app(settings)`) and pass overrides explicitly. Same for the menubar's
`subprocess.Popen(["llm-usage", "dashboard", …])` — fine, but it should pass
`--days` through.

### A6 — Menubar thread-safety
`menubar.py` calls rumps/AppKit mutations (`app.title`, `app.icon`,
`app.menu.clear()`) from a background thread. AppKit is main-thread-only;
this works until it crashes. Marshal UI updates onto the main thread
(`pyobjc` `performSelectorOnMainThread_` or rumps `Timer` instead of a
`threading.Thread` loop).

### A7 — Unofficial-endpoint resilience
Codex `wham/usage`, Cursor dashboard endpoints, and Grok log formats are
unofficial and will drift. Isolate each behind a small parser module with
fixture-based tests (record real anonymized payloads under `tests/fixtures/`)
so drift shows up as a failing test, not a silent zero. `cursor.py`'s
try-4-URLs loop that swallows all exceptions should at least record which
endpoint/status it saw for the error message.

---

## 4. Correctness bugs found (fix regardless of refactor)

| # | Where | Issue |
|---|-------|-------|
| B1 | `cursor.py:145-169` | `reqs` falls back to `totalLinesAdded` (lines-of-code, not requests), then `reqs = max(reqs,0) + composerRequests + …` double-counts when both a generic `requests` field and per-type fields exist. |
| B2 | `cursor.py:292-302` | Nested plan blocks add `report.requests += num` on top of totals already computed from daily rows — double count. |
| B3 | `gemini.py:168-207` | Rows with **no parseable date** are counted into totals (`if day is None or (start <= day <= end)`), so all-time usage inflates every lookback window; also `chat_files` triple-globs overlapping patterns (dedup by `seen` set, but `tmp/**/*.json` can pull in non-chat JSON). |
| B4 | `codex.py:226-229` | `out = output_tokens + reasoning_output_tokens` — verify against Codex CLI: in current rollout format `reasoning_output_tokens` is typically a subset of `output_tokens` (xai.py's comment makes exactly this point for Grok). Likely double-count. |
| B5 | `xai.py:223` | Unknown session → model hard-coded `"grok-4.5"`; and `session_model` maps a session's *current* model onto all its historical events. Label as `"grok (unknown)"` instead of a specific version. |
| B6 | `models.py:78,100` | `datetime.utcnow()` is deprecated and produces naive timestamps; use `datetime.now(timezone.utc)`. |
| B7 | `pricing.py` | Substring matching is fragile (`"o1"`/`"o3"` can match inside unrelated ids; `"codex"` matches `"codex-mini"` fine but also any string containing it). Prefer ordered regex/prefix rules. Prices are hard-coded snapshots — add an `as_of` date shown in the UI, and consider optional refresh from a maintained source (e.g. LiteLLM's pricing JSON) with the local table as fallback. |
| B8 | `claude.py` local scan | Daily points exclude cache tokens while totals include them; sessions counter counts files, not sessions (fine, but rename). |
| B9 | `cli.py:87-91` | `--days` for dashboard only works because collection re-reads env per request; see A5. |
| B10 | `providers/__init__.py:42-45` | `has_codex`/`has_api` truthiness mixes source enum with data presence; a codex report that errored still counts as "has data" and suppresses the API card's notes. Tidy while doing A3. |

---

## 5. Feature suggestions (ranked)

1. **F1 — History & trends.** Persist per-day aggregates to SQLite
   (`~/.config/llm-usage/usage.db`). Enables: sparkline in dashboard cards,
   `llm-usage history --weeks 8`, month-over-month cost, and "burn rate vs
   quota reset" projection ("at this pace you hit 100% Tuesday").
2. **F2 — Quota alerts.** Menubar notification (and optional desktop
   notification from the CLI/daemon) at 70/90% of any quota window; exit code
   support (`llm-usage check --fail-at 90`) so it's scriptable in CI/cron.
3. **F3 — `llm-usage doctor`.** One command that validates each configured
   source end-to-end and prints exactly what's wrong (expired Cursor cookie,
   401 OAuth, missing dirs) — most of `status` exists; add live probes.
4. **F4 — More providers via the A3 plugin interface.** GitHub Copilot
   (local logs + API), OpenRouter (has a clean credits API), Windsurf, Ollama
   (local, free — token counts only). OpenRouter is the easiest win.
5. **F5 — Export formats.** `export --format csv|md` for spreadsheets and
   pasting into docs; JSON schema version field for stability.
6. **F6 — Linux/Windows tray.** The menubar is macOS-only; `pystray` gets you
   Linux/Windows cheaply once A1 (shared snapshot) exists.
7. **F7 — TUI live view.** `llm-usage watch` using rich's Live — cheap, since
   rich is already a dependency.

---

## 6. Suggested implementation order

Each phase is independently shippable; items reference sections above.

**Phase 1 — Security hardening (small diffs, do first)**
1. Escape all dashboard interpolation + CSP header (S1)
2. TrustedHostMiddleware + startup token auth for `/api/*` (S2)
3. Server-side report TTL cache (S5, subset of A1)
4. Stop loading `.env` from CWD by default (S4)
5. Redact `meta` raw payloads from API/export output; 0600 exports (S3)
6. Atomic 0600 cache writes; error-message truncation everywhere (S6)

**Phase 2 — Correctness**
7. Bug fixes B1–B10 with fixture-based tests per parser (A7)
8. CI: ruff + pytest + pip-audit; commit `uv.lock`; Dependabot (S7)

**Phase 3 — Architecture**
9. Provider protocol + shared JSONL accumulator + concurrent collection (A3)
10. Shared snapshot store with per-provider TTL/backoff (A1)
11. Incremental log scanning + SQLite daily aggregates (A2, enables F1)
12. Typed quota/meta models consumed by CLI/dashboard/menubar (A4)
13. App factory, remove env mutation; menubar main-thread UI (A5, A6)

**Phase 4 — Features**
14. F1 history/trends → F2 alerts → F3 doctor → F4 OpenRouter/Copilot → F5–F7

---

## 7. Notes for implementing agents

- Keep the core invariant: **read-only collection, tokens only ever sent to
  their own provider's official host, nothing uploaded anywhere else.**
- All new files under `~/.config/llm-usage/` must be 0600 (dir 0700).
- Unofficial endpoints (ChatGPT wham, Cursor dashboard) must fail soft: an
  error there should never blank out local-log data already collected.
- Preserve the report JSON shape where possible; version it if you must break
  it (`"schema_version": 2`).
- Tests: prefer recorded fixture payloads over mocking httpx internals;
  parsers are where regressions actually happen.
