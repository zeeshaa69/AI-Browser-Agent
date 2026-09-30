"""Structured action schema, expectations and plans.

Every browser interaction is an :class:`Action`. Anything a planner (rule based
or model based) produces is validated here before it can reach the browser.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

ActionType = Literal["navigate", "click", "type", "select", "scroll", "extract", "screenshot", "wait"]
ExtractMode = Literal["text", "table", "list", "links"]

MAX_TEXT_LENGTH = 2000
MAX_WAIT_MS = 30_000


class Action(BaseModel):
    """One controlled browser action."""

    model_config = ConfigDict(extra="forbid")

    type: ActionType
    reason: str = Field(min_length=1, max_length=500, description="Why the action is performed.")
    selector: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    value: str | None = Field(default=None, max_length=500)
    direction: Literal["down", "up", "top", "bottom"] | None = None
    amount: int | None = Field(default=None, ge=1, le=20_000)
    mode: ExtractMode = "text"
    name: str | None = Field(default=None, max_length=80)
    ms: int | None = Field(default=None, ge=0, le=MAX_WAIT_MS)
    full_page: bool = False
    press_enter: bool = False
    fallback_text: str | None = Field(
        default=None,
        max_length=200,
        description="Human-readable label of the target; used to find an alternative element if the selector goes stale.",
    )

    @model_validator(mode="after")
    def _check_fields(self) -> "Action":
        t = self.type
        if t == "navigate":
            _require(self, "url")
            parsed = urlparse(self.url or "")
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("navigate.url must be an absolute http(s) URL")
        elif t == "click":
            _require(self, "selector")
        elif t == "type":
            _require(self, "selector")
            if self.text is None:
                raise ValueError("type requires 'text'")
        elif t == "select":
            _require(self, "selector", "value")
        elif t == "extract":
            _require(self, "selector")
        elif t == "wait":
            if self.ms is None and not self.selector:
                raise ValueError("wait requires 'ms' or 'selector'")
        return self

    @property
    def target(self) -> str:
        """Short human readable description of what the action operates on."""
        if self.type == "navigate":
            return self.url or ""
        if self.type == "scroll":
            return f"{self.direction or 'down'}" + (f" {self.amount}px" if self.amount else "")
        if self.type == "wait":
            return self.selector or f"{self.ms}ms"
        if self.type == "screenshot":
            return self.name or ("full page" if self.full_page else "viewport")
        return self.selector or ""


def _require(action: Action, *fields: str) -> None:
    for f in fields:
        if getattr(action, f) in (None, ""):
            raise ValueError(f"{action.type} requires '{f}'")


class Expectation(BaseModel):
    """What must be true after a step for it to count as done."""

    model_config = ConfigDict(extra="forbid")

    url_contains: str | None = None
    title_contains: str | None = None
    text_contains: str | None = None
    selector_visible: str | None = None
    mode: Literal["all", "any"] = "all"

    def is_empty(self) -> bool:
        return not any((self.url_contains, self.title_contains, self.text_contains, self.selector_visible))


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    description: str = Field(min_length=1, max_length=300)
    action: Action
    expect: Expectation = Field(default_factory=Expectation)
    optional: bool = False


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    steps: list[PlanStep] = Field(min_length=1, max_length=100)
    planner: str = "rules"
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_ids(self) -> "Plan":
        ids = [s.id for s in self.steps]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError("step ids must be sequential starting at 1")
        return self

    def start_url(self) -> str | None:
        for s in self.steps:
            if s.action.type == "navigate":
                return s.action.url
        return None


def parse_action(data: dict) -> Action:
    """Validate a raw dict (e.g. produced by a model) into an Action."""
    return Action.model_validate(data)
