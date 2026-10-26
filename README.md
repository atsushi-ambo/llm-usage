# llm-usage

A small, local view of your AI usage. See your limits in the macOS menu bar, open a dashboard for the full picture, or check from the terminal.

It brings together Codex, Claude Code, Grok Build, Cursor, Muse, Gemini, OpenAI API, and other configured providers. The menu bar stays focused on providers you use; the dashboard shows usage, quota windows, and available cost data.

## Get started

Install from this repository on macOS:

```bash
./install.sh
llm-usage menubar
```

Click the menu bar item to see usage and reset times, switch the provider shown beside the clock, refresh, or open the dashboard. To start it automatically when you log in:

```bash
./scripts/install-menubar-launchagent.sh
```

You can also use the dashboard and CLI directly:

```bash
llm-usage dashboard       # local dashboard at http://127.0.0.1:8765
llm-usage                 # usage summary
llm-usage status          # connected sources
```

The installer uses an editable `uv tool` install. If `llm-usage` is not on your path, add `~/.local/bin` to your shell's `PATH`.

## Menu bar

The item shows one provider and its current quota, for example `Grok 8%`, beside a small usage mark. The mark is a template image, so it takes the same black or white as the other menu bar icons. Open the menu for one line per provider, such as `Grok    Weekly · 75% left`. Rest the pointer on that line to see its windows and reset time. When none are shown, the item reads `AI` so the dashboard and **Refresh Now** remain available.

A provider stays in the menu while one of its quota windows still has usage. A five-hour window keeps it up until that window resets. A weekly window keeps it up until that window resets, including after the five-hour window has already reset. One use, then a couple of hours away, stays on screen for as long as that usage remains in the window. After every open window reports `0%` or reaches its reset time, the provider follows the recent-activity rule until new usage is reported. Providers with no quota window disappear after 30 minutes without an observed usage increase and return when usage changes. The menu checks for updates every 30 seconds; provider reporting can add some delay. A configured source whose refresh fails stays in the menu as **Usage unavailable**, with details in its submenu. Cached quota values are not shown as live percentages or used for quota notifications. Explicit login expiry or rejection also triggers a macOS notification, at most once per provider per day while unresolved. Reminder state survives app restarts and clears after a successful refresh. Network failures and rate limits do not request re-login. Notifications depend on macOS notification permissions and Focus settings.

The hover menu shows Grok's weekly allowance, the Grok Build and Grok Chat split, and the local reset time. Codex shows the reported five-hour and weekly remaining amounts and their local reset times. It does not turn a short burst of use into a misleading daily pace estimate. The menu bar collects only small quota responses, without scanning session logs on each refresh.

## Dashboard and CLI

The dashboard runs on your Mac and shows provider details, usage history, and known costs. Costs estimated from local token counts are marked as estimates; they are not invoices.

```bash
llm-usage -d 7               # last seven days
llm-usage -p codex           # one provider
llm-usage -f json            # JSON output
llm-usage history -w 8       # weekly history
llm-usage --fresh            # skip the saved report
llm-usage export -o out.json
```

For setup and diagnostics, use `llm-usage setup`, `llm-usage validate`, or `llm-usage doctor`. Run `llm-usage --help` for all commands.

## Connect providers

llm-usage reads existing local sessions and sign-ins where available. For provider APIs, add only the keys you need to `~/.config/llm-usage/.env`:

```bash
mkdir -p ~/.config/llm-usage
cp .env.example ~/.config/llm-usage/.env
```

| Source | What llm-usage reads |
| --- | --- |
| Codex | Local sessions and signed-in usage windows |
| Claude Code | Local sessions and available usage windows; optional Anthropic Admin API |
| Grok Build | Local logs and available credit data; optional xAI API |
| Cursor | Admin API or session connection |
| Muse | Weekly usage and reset time from muse.ai; Chrome session on macOS or `MUSE_COOKIE` |
| Gemini | Local CLI logs; optional API connection |
| OpenAI API | Organization usage and costs with an API key |
| OpenRouter | Credit and spend data with an API key |

For Muse, sign in at [muse.ai](https://muse.ai) in Chrome on your Mac. llm-usage reads only the Muse session cookie from Chrome and uses macOS Keychain to decrypt it in memory. Background refreshes do not show Keychain dialogs. If access is blocked, run `llm-usage muse-connect` once and approve the macOS prompt. Set `MUSE_CHROME_IMPORT=false` to disable import. For other browsers or platforms, set `MUSE_COOKIE` to the Cookie header of a signed-in muse.ai request in the private config file; never share or commit it. Session expiry requires signing in again.

Muse reports the weekly percentage, reset time, and plan; llm-usage does not infer token totals or costs. This uses the website's undocumented settings request, so a website change may require an update. Muse Code CLI and Meta developer API usage are separate and are not included.

The `.env.example` file lists the supported keys. llm-usage loads the config file above, not an `.env` in whichever project directory you happen to be using. For a temporary development file, set `LLM_USAGE_ENV_FILE` explicitly.

The dashboard binds to `127.0.0.1` by default and protects usage data with a session token. Collection is read-only; data stays on your Mac unless a configured provider API is queried.

## Update or remove

With the editable install, source changes take effect when the app restarts. To reinstall the command or remove it:

```bash
uv tool install --force -e .
uv tool uninstall llm-usage
```

To stop the login item, unload `~/Library/LaunchAgents/com.llm-usage.menubar.plist` with `launchctl`.

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check src tests
pyright src
```

Licensed under MIT.
