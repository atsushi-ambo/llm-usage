import time
from pathlib import Path

from llm_usage.logcache import scan_with_cache


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
