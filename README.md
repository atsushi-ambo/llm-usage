# llm-usage

A small, local view of your AI usage. See your limits in the macOS menu bar, open a dashboard for the full picture, or check from the terminal.

It brings together Codex, Claude Code, Grok Build, Cursor, Gemini, OpenAI API, and other configured providers. The menu bar stays focused on providers you use; the dashboard shows usage, quota windows, and available cost data.

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

The item shows one provider and its current quota, for example `Codex 80%`. Open the menu for the other active providers, their quota windows, and reset times. When none are shown, the item reads `AI` so the dashboard and **Refresh Now** remain available.

Codex stays visible while its current five-hour window has usage, even if you step away. After the window resets, it follows the recent-activity rule until new usage is reported. Other providers disappear after 30 minutes without an observed usage increase and return when usage changes. The menu checks for updates every 30 seconds; provider reporting can add some delay.

Codex shows the reported five-hour and weekly percentages, remaining allowance, and local reset times. It does not turn a short burst of use into a misleading daily pace estimate. The menu bar collects only small quota responses, without scanning session logs on each refresh.

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
| Gemini | Local CLI logs; optional API connection |
| OpenAI API | Organization usage and costs with an API key |
| OpenRouter | Credit and spend data with an API key |

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
