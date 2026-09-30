"""Data models exchanged between observer, verifier, agent and UI."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ElementInfo(BaseModel):
    """What the agent knows about a page element (used by safety + recovery)."""

    selector: str = ""
    tag: str = ""
    role: str = ""
    text: str = ""
    type: str = ""
    name: str = ""
    id: str = ""
    href: str = ""
    aria_label: str = ""
    placeholder: str = ""
    autocomplete: str = ""
    form_action: str = ""
    in_form: bool = False
    visible: bool = True
    enabled: bool = True

    def haystack(self) -> str:
        """Lower-cased concatenation of every descriptive attribute."""
        parts = [self.text, self.name, self.id, self.aria_label, self.placeholder, self.href, self.form_action, self.value_hint()]
        return " ".join(p for p in parts if p).lower()

    def value_hint(self) -> str:
        return self.autocomplete


class Observation(BaseModel):
    url: str
    title: str
    text: str
    headings: list[str] = Field(default_factory=list)
    elements: list[ElementInfo] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    overlay: bool = False
    screenshot: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "headings": self.headings[:5],
            "elements": len(self.elements),
            "tables": len(self.tables),
            "overlay": self.overlay,
            "screenshot": self.screenshot,
        }


class ActionOutcome(BaseModel):
    """Raw result of executing one action in the browser."""

    detail: str = ""
    data: Any = None
    screenshot: str | None = None


class VerifyResult(BaseModel):
    ok: bool
    checked: bool = True
    detail: str = ""
