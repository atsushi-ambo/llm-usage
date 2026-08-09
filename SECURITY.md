# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately via
[GitHub Security Advisories](https://github.com/atsushi-ambo/llm-usage/security/advisories/new)
rather than opening a public issue. You should get a response within a week.

## Scope and threat model

llm-usage is a **local, single-user tool**. It:

- reads other tools' credential stores (`~/.claude/.credentials.json` /
  macOS Keychain, `~/.codex/auth.json`, `~/.grok/auth.json`) and sends each
  token **only to its own provider's API** — never anywhere else. Changes
  that break this invariant are treated as vulnerabilities.
- never uploads usage data; the dashboard binds to `127.0.0.1` and requires
  a per-run token, and refuses non-loopback binds without an explicit
  opt-in flag.
- writes caches/exports under `~/.config/llm-usage/` with `0600`/`0700`
  permissions.

Reports about weakening any of the above — token leakage into logs, error
messages, exports, or the API; dashboard auth bypass; DNS-rebinding — are
in scope and appreciated.

## Plugins run with full process privileges

Custom provider plugins (`~/.config/llm-usage/plugins/*.py`) are **arbitrary
Python executed in-process**. That process reads live OAuth tokens and every
configured API key, so a plugin is exactly as trusted as llm-usage itself —
there is no sandbox. Install only plugins you have read and trust, the same
way you would treat a shell profile.

The loader enforces the "user-owned directory" assumption rather than taking
it on faith. It refuses to execute a plugin when:

- the file resolves outside the plugins directory (blocks path traversal and
  symlinks that smuggle in code from elsewhere);
- the plugins directory or the plugin file is writable by group or others;
- either is owned by someone other than you or root.

Skipped plugins surface as a visible error row rather than disappearing
silently, so tampering is noticed instead of being mistaken for a plugin
that simply stopped working. A way to bypass these checks — or to get code
executed from outside the plugins directory — is a vulnerability worth
reporting.
