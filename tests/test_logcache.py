import json
import time
from pathlib import Path

from llm_usage.logcache import prune_missing_sources, scan_with_cache
from llm_usage import quota


def test_unchanged_file_is_not_reparsed(tmp_path: Path):
    f = tmp_path / "log.jsonl"
    f.write_text("hello\n")

    calls = []

    def parse_fn(path: Path) -> list[str]:
        calls.append(path)
        return [path.read_text()]

    first = scan_with_cache("test-ns", f, parse_fn)
    second = scan_with_cache("test-ns", f, parse_fn)

    assert first == second == ["hello\n"]
    assert len(calls) == 1  # second call served from cache


def test_changed_file_is_reparsed(tmp_path: Path):
    f = tmp_path / "log.jsonl"
    f.write_text("hello\n")

    calls = []

    def parse_fn(path: Path) -> list[str]:
        calls.append(path)
        return [path.read_text()]

    scan_with_cache("test-ns", f, parse_fn)
    # Ensure mtime actually advances on filesystems with coarse resolution.
    time.sleep(0.01)
    f.write_text("hello again, and more\n")
    result = scan_with_cache("test-ns", f, parse_fn)

    assert result == ["hello again, and more\n"]
    assert len(calls) == 2


def test_different_namespaces_do_not_collide(tmp_path: Path):
    f = tmp_path / "log.jsonl"
    f.write_text("data\n")

    a = scan_with_cache("ns-a", f, lambda p: ["a"])
    b = scan_with_cache("ns-b", f, lambda p: ["b"])

    assert a == ["a"]
    assert b == ["b"]


def test_missing_file_falls_through_to_parse_fn(tmp_path: Path):
    missing = tmp_path / "does-not-exist.jsonl"
    result = scan_with_cache("test-ns", missing, lambda p: ["called anyway"])
    assert result == ["called anyway"]


def test_logscan_writes_compact_json_with_source_path(tmp_path: Path):
    f = tmp_path / "log.jsonl"
    f.write_text("x\n")
    scan_with_cache("compact-ns", f, lambda p: {"lines": 1})

    cache_files = list((quota.cache_dir() / "logscan" / "compact-ns").glob("*.json"))
    assert len(cache_files) == 1
    raw = cache_files[0].read_text(encoding="utf-8")
    # Compact: no pretty-print indentation
    assert "\n  " not in raw
    cached = json.loads(raw)
    assert cached["path"] == str(f)
    assert cached["data"] == {"lines": 1}


def test_prune_drops_entries_for_deleted_sources(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    f.write_text("session\n")
    scan_with_cache("prune-ns", f, lambda p: ["ok"])
    cache_dir = quota.cache_dir() / "logscan" / "prune-ns"
    assert list(cache_dir.glob("*.json"))

    f.unlink()
    removed = prune_missing_sources("prune-ns")
    assert removed == 1
    assert list(cache_dir.glob("*.json")) == []
