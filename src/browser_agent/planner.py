"""Planners: natural-language goal -> validated :class:`Plan`.

``RulesPlanner`` is deterministic and needs no model. ``OllamaPlanner`` asks a
*local* model (Ollama on loopback) for a JSON plan, validates it against the
action schema and falls back to the rules planner if the model is unavailable or
returns something invalid. Nothing here talks to a cloud service.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Protocol
from urllib.parse import urlparse

from pydantic import ValidationError

from .actions import Action, Expectation, Plan, PlanStep
from .errors import PlanningError

_URL = re.compile(r"https?://[^\s\"'<>,)]+", re.I)
_STOP = {"the", "a", "an", "all", "any", "some", "this", "that", "its", "their", "website", "site", "web", "local", "demo", "structured", "json", "csv", "markdown", "out"}
_PAGE_PHRASE = re.compile(
    r"\b(?:find|open|go\s+to|navigate\s+to|visit|click(?:\s+on)?|look\s+for|locate)\s+(?:the\s+)?([\w&' -]{2,40}?)\s+(?:page|link|section|tab)\b", re.I
)
_EXTRACT_PHRASE = re.compile(
    r"\b(?:extract|scrape|get|read|collect|copy|grab|save)\s+(?:the\s+|all\s+)?([\w&' -]{2,40}?)\s+(table|list|links|text|data|information|info|details|prices|plans)\b", re.I
)
_FIELD = re.compile(r"\b(name|email|message|phone|company|topic)\s*(?:=|:|is)\s*(?:\"([^\"]*)\"|'([^']*)'|([^,;\n]+?))(?=\s*(?:,|;|\band\b\s+\w+\s*(?:=|:)|$))", re.I)
_SELECT_PHRASE = re.compile(r"\bselect\s+[\"']?([\w &-]+?)[\"']?\s+(?:in|from|for)\s+(?:the\s+)?([\w -]+?)\s*(?:dropdown|select|filter|menu|field)\b", re.I)


class Planner(Protocol):
    name: str

    def plan(self, goal: str, start_url: str | None = None) -> Plan: ...


def _clean_words(phrase: str) -> str:
    words = [w for w in re.findall(r"[\w&']+", phrase) if w.lower() not in _STOP]
    return " ".join(words)


def _link_label(label: str) -> str:
    # "plans & pricing" -> match on the most specific word
    return label.strip()


class RulesPlanner:
    """Keyword/pattern planner. Understands a bounded set of task shapes."""

    name = "rules"

    def __init__(self, demo_url: str | None = None) -> None:
        self.demo_url = demo_url

    def plan(self, goal: str, start_url: str | None = None) -> Plan:
        if not goal or not goal.strip():
            raise PlanningError("goal is empty")
        g = goal.strip()
        notes: list[str] = []
        steps: list[PlanStep] = []

        def add(desc: str, action: Action, expect: Expectation | None = None, optional: bool = False) -> None:
            steps.append(PlanStep(id=len(steps) + 1, description=desc, action=action, expect=expect or Expectation(), optional=optional))

        # 1. where to start
        url = start_url
        if not url:
            m = _URL.search(g)
            url = m.group(0).rstrip(".") if m else None
        if not url and re.search(r"\b(local\s+)?demo\s+(web)?site\b|\bdemo\s+(app|page)\b|\blocal\s+test\s+site\b", g, re.I):
            url = self.demo_url
            if not url:
                raise PlanningError("the goal refers to the local demo site but no demo URL is configured (start it with `browser-agent demo` and pass --url)")
        if not url:
            raise PlanningError("the goal does not name a website. Include a URL in the goal or pass --url; the agent never guesses a site.")
        host = urlparse(url).hostname
        if urlparse(url).scheme not in ("http", "https") or not host:
            raise PlanningError(f"invalid start URL: {url}")
        add(f"Open {url}", Action(type="navigate", url=url, reason="start page for the goal"), Expectation(url_contains=host))

        # 2. navigate to the requested page
        page_label: str | None = None
        m = _PAGE_PHRASE.search(g)
        if m:
            page_label = _clean_words(m.group(1)) or None
        extract_m = _EXTRACT_PHRASE.search(g)
        if not page_label and extract_m:
            cand = _clean_words(extract_m.group(1))
            if cand and cand.lower() not in ("data", "information", "info", "details", "all"):
                page_label = cand
        first_word = page_label.split()[0].lower() if page_label else None
        if page_label and first_word:
            add(
                f"Open the {page_label} page",
                Action(
                    type="click",
                    selector=f'a:has-text("{_link_label(page_label)}")',
                    fallback_text=page_label,
                    reason=f"navigate to the {page_label} page",
                ),
                Expectation(url_contains=first_word, title_contains=first_word, text_contains=None, mode="any"),
            )

        # 3. filters / dropdowns
        for sm in _SELECT_PHRASE.finditer(g):
            value, field = sm.group(1).strip(), sm.group(2).strip()
            slug = field.lower().replace(" ", "-")
            add(
                f"Select {value!r} in the {field} dropdown",
                Action(type="select", selector=f'select[name="{slug}"]', value=value, fallback_text=field, reason=f"filter by {field}"),
            )

        # 4. load more (dynamic content)
        if re.search(r"\bload\s+more\b|\bshow\s+more\b", g, re.I):
            add(
                "Load more rows",
                Action(type="click", selector='button:has-text("Load more")', fallback_text="Load more", reason="reveal additional dynamic content"),
                optional=True,
            )
            add("Wait for the new rows to render", Action(type="wait", ms=700, reason="dynamic rows are appended asynchronously"))

        # 5. forms
        fields = {}
        if re.search(r"\b(fill|complete|submit|send)\b.*\b(form|message)\b", g, re.I) or re.search(r"\bform\b", g, re.I):
            for fm in _FIELD.finditer(g):
                val = fm.group(2) or fm.group(3) or fm.group(4) or ""
                fields[fm.group(1).lower()] = val.strip()
        if fields:
            for key, val in fields.items():
                add(
                    f"Fill the {key} field",
                    Action(type="type", selector=f'[name="{key}"]', text=val, fallback_text=key, reason=f"enter {key} as requested"),
                )
            add(
                "Submit the form",
                Action(type="click", selector='form button[type="submit"]', fallback_text="submit", reason="submit the form the user asked to send"),
                Expectation(selector_visible='[role="status"], .success'),
            )
            notes.append("Submitting a form is a sensitive action and requires confirmation.")

        # 6. extraction
        wants_extract = bool(extract_m) or re.search(r"\b(extract|scrape|collect|table|pricing information)\b", g, re.I)
        if wants_extract:
            kind = (extract_m.group(2).lower() if extract_m else "data")
            label = _clean_words(extract_m.group(1)) if extract_m else (page_label or "data")
            name = (label.split()[0].lower() if label else "data") or "data"
            if kind == "table" or re.search(r"\btable\b", g, re.I) or kind in ("prices", "plans") or name == "pricing":
                action = Action(type="extract", selector="table", mode="table", name=name, fallback_text=name, reason=f"extract the {label or name} table")
            elif kind == "list":
                action = Action(type="extract", selector="ul, ol", mode="list", name=name, reason=f"extract the {label or name} list")
            elif kind == "links":
                action = Action(type="extract", selector="main, body", mode="links", name=name, reason="extract links")
            else:
                action = Action(type="extract", selector="main", mode="text", name=name, reason=f"extract the {label or name} text")
            add(f"Extract the {label or name} data", action)

        # 7. misc
        if re.search(r"\bscroll\b.*\b(bottom|end)\b", g, re.I):
            add("Scroll to the bottom", Action(type="scroll", direction="bottom", reason="user asked to scroll to the bottom"))
        if re.search(r"\bscreenshot\b", g, re.I):
            add("Take a screenshot", Action(type="screenshot", name="requested", full_page=True, reason="user asked for a screenshot"))

        if len(steps) == 1:
            notes.append("No actions beyond navigation were recognised in the goal; the plan only opens the page.")
        return Plan(goal=g, steps=steps, planner=self.name, notes=notes)


def _is_loopback(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in ("localhost", "127.0.0.1", "::1", "[::1]")


_SYSTEM_PROMPT = """You convert a browsing goal into a JSON plan for a controlled browser agent.
Reply with ONE JSON object and nothing else, shaped exactly like:
{"steps": [{"description": str, "action": {...}, "expect": {...}}]}
Allowed action types: navigate(url), click(selector), type(selector,text), select(selector,value),
scroll(direction: up|down|top|bottom), extract(selector, mode: text|table|list|links, name), screenshot, wait(ms or selector).
Every action needs a short "reason". Use Playwright selectors such as a:has-text("Pricing").
"expect" may hold url_contains, title_contains, text_contains, selector_visible.
Never plan purchases, account deletion, or typing passwords/card numbers."""


class OllamaPlanner:
    """Asks a local Ollama model for a plan; validates it; falls back to rules."""

    name = "ollama"

    def __init__(self, model: str, endpoint: str = "http://127.0.0.1:11434", fallback: RulesPlanner | None = None, timeout_s: float = 60.0):
        if not _is_loopback(endpoint):
            raise PlanningError("the model endpoint must be on this machine (localhost/127.0.0.1); cloud endpoints are not supported")
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.fallback = fallback or RulesPlanner()
        self.timeout_s = timeout_s
        self.last_error: str | None = None

    def _ask(self, goal: str, start_url: str | None) -> dict:
        body = json.dumps(
            {
                "model": self.model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"Goal: {goal}\nStart URL: {start_url or 'not given'}"},
                ],
            }
        ).encode()
        req = urllib.request.Request(f"{self.endpoint}/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 - loopback only, checked in __init__
            payload = json.loads(resp.read())
        return json.loads(payload["message"]["content"])

    def plan(self, goal: str, start_url: str | None = None) -> Plan:
        self.last_error = None
        try:
            raw = self._ask(goal, start_url)
            steps = []
            for i, s in enumerate(raw["steps"], start=1):
                steps.append(PlanStep(id=i, description=s.get("description") or s["action"].get("reason", "step"), action=Action.model_validate(s["action"]), expect=Expectation.model_validate(s.get("expect") or {})))
            plan = Plan(goal=goal, steps=steps, planner=f"ollama:{self.model}")
            if plan.steps[0].action.type != "navigate" and not start_url:
                raise ValueError("model plan does not begin with navigation and no start URL was given")
            return plan
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, TypeError, ValueError, ValidationError) as exc:
            self.last_error = f"{exc.__class__.__name__}: {exc}"
            plan = self.fallback.plan(goal, start_url)
            plan.notes.append(f"local model planner unavailable or invalid ({self.last_error}); used deterministic rules planner")
            return plan


def build_planner(kind: str = "rules", *, demo_url: str | None = None, model: str | None = None, endpoint: str = "http://127.0.0.1:11434") -> Planner:
    rules = RulesPlanner(demo_url=demo_url)
    if kind == "rules":
        return rules
    if kind == "ollama":
        if not model:
            raise PlanningError("--model is required for the ollama planner")
        return OllamaPlanner(model, endpoint, fallback=rules)
    raise PlanningError(f"unknown planner {kind!r}")


class StaticPlanner:
    """Returns a pre-built plan. Useful for scripted runs and tests."""

    name = "static"

    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    def plan(self, goal: str, start_url: str | None = None) -> Plan:
        return self._plan
