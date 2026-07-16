"""Per-file cache for local log scanning.

Local log directories (~/.claude/projects, ~/.codex/sessions, ~/.grok/logs,
~/.gemini) can accumulate months of history. Every `llm-usage` invocation —
including the menubar's poll every 2 minutes — re-read and re-parsed every
matching file on every call, so collection cost grew without bound as
history piled up. This caches each file's parsed result keyed by an
(mtime, size) fingerprint, so a file only gets re-parsed when it actually
changed since the last call.

`parse_fn` must parse the WHOLE file with no date-range filtering (the
caller applies --days filtering afterward) so one cached parse stays valid
across different lookback windows.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from llm_usage import quota


def _cache_file_for(namespace: str, path: Path) -> Path:
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:24]
    d = quota.cache_dir() / "logscan" / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{digest}.json"


def scan_with_cache(namespace: str, path: Path, parse_fn: Callable[[Path], Any]) -> Any:
    """Return parse_fn(path), reusing a cached result when path's
    (mtime, size) fingerprint matches what we cached last time."""
    try:
        st = path.stat()
    except OSError:
        return parse_fn(path)
    mtime, size = st.st_mtime, st.st_size

    cache_path = _cache_file_for(namespace, path)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
        if (
            isinstance(cached, dict)
            and cached.get("mtime") == mtime
            and cached.get("size") == size
            and "data" in cached
        ):
            return cached["data"]

    data = parse_fn(path)
    try:
        quota.atomic_write_json(cache_path, {"mtime": mtime, "size": size, "data": data})
    except OSError:
        pass
    return data
