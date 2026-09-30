import csv
import io
import json

import pytest

from browser_agent import extraction

ROWS = [{"Plan": "Starter", "Price": "$9 / month"}, {"Plan": "Pro | Plus", "Price": "$29"}]


def test_json_roundtrip():
    assert json.loads(extraction.render({"pricing": ROWS}, "json")) == {"pricing": ROWS}


def test_csv_single_dataset():
    out = extraction.render({"pricing": ROWS}, "csv")
    assert list(csv.DictReader(io.StringIO(out))) == ROWS


def test_csv_multiple_datasets_have_sections():
    out = extraction.render({"a": ROWS, "b": ["x", "y"]}, "csv")
    assert "# a" in out and "# b" in out and "value" in out


def test_csv_union_of_columns():
    out = extraction.render({"d": [{"a": 1}, {"b": 2}]}, "csv")
    assert out.splitlines()[0] == "a,b"


def test_markdown_table_escapes_pipes():
    out = extraction.render({"pricing": ROWS}, "markdown")
    assert "| Plan | Price |" in out and "Pro \\| Plus" in out and out.startswith("## pricing")


def test_markdown_text_and_list():
    out = extraction.render({"t": "hello", "l": ["a", "b"]}, "markdown")
    assert "hello" in out and "- a" in out


def test_unknown_format():
    with pytest.raises(ValueError):
        extraction.render({}, "xml")


def test_normalize_payload():
    assert extraction.normalize_payload({"rows": [1]}) == [1]
    assert extraction.normalize_payload({"text": "x"}) == "x"
    with pytest.raises(ValueError):
        extraction.normalize_payload({"error": "no table"})


def test_extension_for():
    assert extraction.extension_for("markdown") == "md"
