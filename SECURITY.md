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
