import json
import threading

from browser_agent.logs import ExecutionLog


def test_entries_are_persisted_as_jsonl(tmp_path):
    seen = []
    log = ExecutionLog(tmp_path / "log.jsonl", on_entry=seen.append)
    log.add(step=1, action="click", target="#a", result="failed", error="boom", retry_count=2, duration_ms=12)
    log.add(step=1, action="click", target="#a", result="ok", retry_count=2)
    lines = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert [r["result"] for r in lines] == ["failed", "ok"]
    assert lines[0]["error"] == "boom" and lines[0]["retry_count"] == 2 and "timestamp" in lines[0]
    assert len(seen) == 2 and len(log.errors()) == 1


def test_thread_safe(tmp_path):
    log = ExecutionLog(tmp_path / "l.jsonl")
    ts = [threading.Thread(target=lambda: [log.add(action="wait", result="ok") for _ in range(50)]) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(log.entries) == 200 == len((tmp_path / "l.jsonl").read_text().splitlines())
