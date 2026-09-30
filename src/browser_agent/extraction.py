"""Structured extraction helpers: JSON / CSV / Markdown rendering."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

FORMATS = ("json", "csv", "markdown")

# Runs in the page: returns structured data for an element.
EXTRACT_JS = r"""
(el, mode) => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  if (mode === 'table') {
    const table = el.tagName === 'TABLE' ? el : el.querySelector('table');
    if (!table) return { error: 'no <table> found inside the selected element' };
    let headerCells = Array.from(table.querySelectorAll('thead th'));
    let rows = Array.from(table.querySelectorAll('tbody tr'));
    if (!headerCells.length) {
      const all = Array.from(table.querySelectorAll('tr'));
      if (all.length) { headerCells = Array.from(all[0].children); rows = all.slice(1); }
    }
    if (!rows.length) rows = Array.from(table.querySelectorAll('tr')).filter((r) => !r.querySelector('th'));
    const headers = headerCells.map((h, i) => clean(h.innerText) || ('column_' + (i + 1)));
    const data = rows
      .filter((r) => r.getBoundingClientRect().height > 0)
      .map((r) => {
        const cells = Array.from(r.children).map((c) => clean(c.innerText));
        const obj = {};
        cells.forEach((c, i) => { obj[headers[i] || ('column_' + (i + 1))] = c; });
        return obj;
      });
    return { rows: data };
  }
  if (mode === 'list') {
    const items = Array.from(el.querySelectorAll('li')).map((li) => clean(li.innerText)).filter(Boolean);
    return { items };
  }
  if (mode === 'links') {
    const links = Array.from(el.tagName === 'A' ? [el] : el.querySelectorAll('a[href]')).map((a) => ({
      text: clean(a.innerText), href: a.href,
    }));
    return { links };
  }
  return { text: clean(el.innerText) };
}
"""


def normalize_payload(raw: dict[str, Any]) -> Any:
    """Turn the raw JS result into the stored value (rows list / list / text)."""
    if "error" in raw:
        raise ValueError(raw["error"])
    for key in ("rows", "items", "links", "text"):
        if key in raw:
            return raw[key]
    raise ValueError("unrecognised extraction payload")


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list) and all(isinstance(v, dict) for v in value):
        return value
    if isinstance(value, list):
        return [{"value": v} for v in value]
    return [{"text": value}]


def to_json(results: dict[str, Any]) -> str:
    return json.dumps(results, indent=2, ensure_ascii=False)


def _csv_of(rows: list[dict[str, Any]]) -> str:
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def to_csv(results: dict[str, Any]) -> str:
    if len(results) == 1:
        return _csv_of(_rows(next(iter(results.values()))))
    return "\n".join(f"# {name}\n{_csv_of(_rows(v))}" for name, v in results.items())


def _md_escape(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def _md_table(rows: list[dict[str, Any]]) -> str:
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    if not cols:
        return "_(no data)_\n"
    lines = ["| " + " | ".join(_md_escape(c) for c in cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(_md_escape(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def to_markdown(results: dict[str, Any]) -> str:
    out = []
    for name, value in results.items():
        out.append(f"## {name}\n")
        if isinstance(value, str):
            out.append(value + "\n")
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            out.append(_md_table(value))
        elif isinstance(value, list):
            out.append("\n".join(f"- {_md_escape(v)}" for v in value) + "\n")
        else:
            out.append(f"{value}\n")
    return "\n".join(out)


def render(results: dict[str, Any], fmt: str = "json") -> str:
    fmt = fmt.lower()
    if fmt == "json":
        return to_json(results)
    if fmt == "csv":
        return to_csv(results)
    if fmt in ("markdown", "md"):
        return to_markdown(results)
    raise ValueError(f"unknown format {fmt!r}; choose one of {FORMATS}")


def extension_for(fmt: str) -> str:
    return {"json": "json", "csv": "csv", "markdown": "md", "md": "md"}[fmt.lower()]
