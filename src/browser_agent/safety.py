"""Safety policy: domain rules, hard denials, confirmation and limits.

The policy is enforced in three places: before an action runs (``assess`` and
``check_url``), on every network request the browser makes (see
``BrowserController``) and on the page the browser ends up on.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable
from urllib.parse import urlparse

from .actions import Action
from .errors import SafetyViolation
from .models import ElementInfo


class Verdict(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass
class Decision:
    verdict: Verdict
    reason: str = ""
    category: str = ""


ALLOW = Decision(Verdict.ALLOW)

_FINANCIAL = re.compile(
    r"\b(buy(\s+now)?|purchase|pay(ment)?(\s+now)?|place\s+(your\s+)?order|complete\s+(your\s+)?order|"
    r"check\s?out|donate|send\s+money|transfer\s+funds?|withdraw|confirm\s+payment|add\s+to\s+cart|"
    r"subscribe\s+and\s+pay|upgrade\s+now)\b"
)
_DELETION = re.compile(
    r"\b(delete|close|deactivate|terminate|remove|cancel)\b[\s\w-]{0,20}\baccount\b|delete[-_]account|account[-_]deletion"
)
_CREDENTIAL = re.compile(r"pass(word|wd|code)?\b|\bcvv\b|\bcvc\b|card[-_\s]?(number|no)|cc[-_]?(number|num|csc|exp)|\bssn\b|\botp\b|\bpin\b|secret|api[-_]?key|token")
_SUBMIT = re.compile(r"\b(submit|send|subscribe|sign\s?up|register|post|save|apply|confirm)\b")


def normalize_domain(value: str) -> str:
    v = value.strip().lower()
    if "://" in v:
        v = urlparse(v).hostname or ""
    v = v.lstrip("*").lstrip(".").split("/")[0]
    return v.split(":")[0] if not v.startswith("[") else v


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def domain_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


@dataclass
class ConfirmRequest:
    action: Action
    reason: str
    element: ElementInfo | None = None
    url: str = ""

    def describe(self) -> str:
        what = self.element.text or self.element.selector if self.element else self.action.target
        return f"{self.action.type} on {what!r} at {self.url}: {self.reason}"


Confirmer = Callable[[ConfirmRequest], bool]


def deny_all(_: ConfirmRequest) -> bool:
    return False


def approve_all(_: ConfirmRequest) -> bool:
    """Approves *confirmable* actions only. Hard denials never reach a confirmer."""
    return True


def interactive_confirmer(req: ConfirmRequest) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        return False
    print(f"\n[confirmation required] {req.describe()}", file=sys.stderr)
    try:
        return input("Allow this action? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


@dataclass
class SafetyPolicy:
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    max_actions: int = 40
    action_timeout_ms: int = 10_000
    task_timeout_s: float = 180.0
    require_confirmation: bool = True

    def __post_init__(self) -> None:
        self.allowed_domains = [d for d in (normalize_domain(d) for d in self.allowed_domains) if d]
        self.blocked_domains = [d for d in (normalize_domain(d) for d in self.blocked_domains) if d]

    # ---- domains -------------------------------------------------------
    def allow_domain(self, domain: str) -> None:
        d = normalize_domain(domain)
        if d and d not in self.allowed_domains:
            self.allowed_domains.append(d)

    def url_allowed(self, url: str) -> bool:
        return self.url_problem(url) is None

    def url_problem(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"scheme {parsed.scheme!r} is not allowed"
        host = (parsed.hostname or "").lower()
        if not host:
            return "URL has no host"
        for d in self.blocked_domains:
            if domain_matches(host, d):
                return f"domain {host!r} is blocked"
        if not any(domain_matches(host, d) for d in self.allowed_domains):
            return f"domain {host!r} is not in the allowed list"
        return None

    def check_url(self, url: str) -> None:
        problem = self.url_problem(url)
        if problem:
            raise SafetyViolation(f"{problem}: {url}")

    # ---- actions -------------------------------------------------------
    def assess(self, action: Action, element: ElementInfo | None = None) -> Decision:
        if action.type == "navigate":
            problem = self.url_problem(action.url or "")
            return Decision(Verdict.DENY, problem, "domain") if problem else ALLOW

        if element is None or action.type not in ("click", "type", "select", "extract"):
            return ALLOW

        hay = element.haystack()
        is_secret_field = element.type in ("password",) or bool(
            _CREDENTIAL.search(" ".join((element.name, element.id, element.autocomplete, element.aria_label, element.placeholder)).lower())
        )

        if action.type in ("type", "select", "extract") and is_secret_field:
            return Decision(Verdict.DENY, "credential / payment fields are never read or filled by the agent", "credentials")

        if action.type == "click":
            if _DELETION.search(hay):
                return Decision(Verdict.DENY, "account deletion is never performed automatically", "account_deletion")
            if _FINANCIAL.search(hay):
                return Decision(Verdict.DENY, "financial transactions are never performed automatically", "financial")
            if self.require_confirmation and (element.type == "submit" or (element.tag == "button" and element.in_form and element.type in ("", "submit")) or _SUBMIT.search(element.text.lower())):
                return Decision(Verdict.CONFIRM, "submits data to a website", "submit")

        if action.type == "type" and action.press_enter and element.in_form and self.require_confirmation:
            return Decision(Verdict.CONFIRM, "pressing Enter submits the form", "submit")
        return ALLOW
