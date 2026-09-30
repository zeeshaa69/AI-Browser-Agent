"""Execution log: one structured entry per action attempt."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field

Result = Literal["ok", "failed", "blocked", "retry", "skipped", "info"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class LogEntry(BaseModel):
    timestamp: str = Field(default_factory=now_iso)
    step: int | None = None
    action: str
    target: str = ""
    result: Result
    error: str | None = None
    retry_count: int = 0
    duration_ms: int = 0
    verified: bool | None = None
    detail: str = ""
    screenshot: str | None = None


class ExecutionLog:
    """Thread-safe in-memory log mirrored to a JSONL file."""

    def __init__(self, path: Path | str | None = None, on_entry: Callable[[LogEntry], None] | None = None):
        self.path = Path(path) if path else None
        self.entries: list[LogEntry] = []
        self._lock = threading.Lock()
        self._on_entry = on_entry
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def add(self, **fields) -> LogEntry:  # type: ignore[no-untyped-def]
        entry = LogEntry(**fields)
        with self._lock:
            self.entries.append(entry)
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(entry.model_dump_json() + "\n")
        if self._on_entry:
            self._on_entry(entry)
        return entry

    def errors(self) -> list[LogEntry]:
        return [e for e in self.entries if e.error]

    def as_dicts(self) -> list[dict]:
        return [e.model_dump() for e in self.entries]
