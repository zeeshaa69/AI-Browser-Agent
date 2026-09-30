"""Recovery strategies: what to do when an attempt fails or does not verify."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from .actions import Action
from .errors import ErrorKind
from .models import ElementInfo, Observation

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_HAS_TEXT = re.compile(r"""(?:has-text|text(?:-is)?|text)\s*[=(]\s*["']?([^"')\]]+)""", re.I)
_NAME_ATTR = re.compile(r"""\[(?:name|id|placeholder|aria-label)\s*[\^$*~]?=\s*["']?([^"'\]]+)""", re.I)


@dataclass
class Recovery:
    description: str
    ops: list[tuple[str, dict]] = field(default_factory=list)  # ("wait"|"reload"|"back"|"dismiss_overlay", params)
    replacement: Action | None = None
    reverify_only: bool = False
    find_after_ops: bool = False
    timeout_factor: float = 1.0


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def target_words(action: Action) -> list[str]:
    """Words describing what the action is aiming at."""
    words: list[str] = []
    if action.fallback_text:
        words += tokens(action.fallback_text)
    if action.selector:
        for m in _HAS_TEXT.finditer(action.selector):
            words += tokens(m.group(1))
        for m in _NAME_ATTR.finditer(action.selector):
            words += tokens(m.group(1))
        m = re.match(r"^#([\w-]+)", action.selector)
        if m:
            words += [w for w in tokens(m.group(1).replace("-", " ").replace("_", " ")) if not w.isdigit()]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return seen


_KIND_TAGS = {
    "click": {"a", "button", "summary", "input"},
    "type": {"input", "textarea"},
    "select": {"select"},
}


def score_element(words: list[str], el: ElementInfo) -> float:
    if not words:
        return 0.0
    hay_tokens = set(tokens(" ".join((el.text, el.id.replace("-", " "), el.name, el.aria_label, el.placeholder, el.href.replace("/", " ").replace(".", " ")))))
    hits = sum(1 for w in words if w in hay_tokens)
    ratio = max((difflib.SequenceMatcher(None, " ".join(words), el.text.lower()).ratio() if el.text else 0.0), 0.0)
    return hits / len(words) + 0.25 * ratio


def find_alternative(action: Action, obs: Observation, exclude: set[str]) -> ElementInfo | None:
    if action.type == "extract":
        return None
    words = target_words(action)
    tags = _KIND_TAGS.get(action.type, set())
    best: tuple[float, ElementInfo] | None = None
    for el in obs.elements:
        if el.selector in exclude or (tags and el.tag not in tags and el.role not in ("button", "link")):
            continue
        if action.type == "type" and el.type in ("submit", "button", "checkbox", "radio", "hidden"):
            continue
        s = score_element(words, el)
        if s >= 0.5 and (best is None or s > best[0]):
            best = (s, el)
    return best[1] if best else None


class RecoveryPlanner:
    def __init__(self, controller) -> None:  # type: ignore[no-untyped-def]
        self.c = controller

    def propose(
        self,
        action: Action,
        kind: ErrorKind,
        message: str,
        attempt: int,
        *,
        tried: set[str],
        url_before: str = "",
        max_attempts: int = 3,
    ) -> Recovery | None:
        """``attempt`` is 1 for the first recovery. Returns None when nothing sensible is left."""
        if attempt > max_attempts or kind in (ErrorKind.POLICY, ErrorKind.LIMIT, ErrorKind.CANCELLED):
            return None

        if kind == ErrorKind.ELEMENT_NOT_FOUND:
            if attempt == 1:
                return Recovery("wait for dynamic content, then retry the same selector", ops=[("wait", {"ms": 700})])
            rec = self._alternative(action, tried)
            if rec is None:
                return Recovery("no alternative element found; wait longer and retry the same selector", ops=[("wait", {"ms": 1000})])
            return rec

        if kind == ErrorKind.INTERCEPTED:
            if self.c.dismiss_candidates():
                return Recovery("dismiss the overlay that is blocking the click", ops=[("dismiss_overlay", {})])
            return Recovery("wait for the blocking element to go away", ops=[("wait", {"ms": 800})]) if attempt < 3 else None

        if kind == ErrorKind.TIMEOUT:
            if attempt == 1:
                return Recovery("retry with a longer timeout", ops=[("wait", {"ms": 300})], timeout_factor=2.0)
            if action.type != "navigate" and attempt == 2:
                return Recovery("reload the page and retry", ops=[("reload", {})], timeout_factor=2.0)
            return Recovery("retry once more with the longest timeout", timeout_factor=3.0) if attempt == 3 else None

        if kind == ErrorKind.NAVIGATION:
            if "domain policy" in message or "not in the allowed" in message or "is blocked" in message:
                return None
            return Recovery("wait and retry the navigation", ops=[("wait", {"ms": 800 * attempt})])

        if kind == ErrorKind.VERIFICATION:
            return self._after_failed_verification(action, attempt, tried, url_before)

        if attempt == 1:
            return Recovery("retry once", ops=[("wait", {"ms": 300})])
        return None

    # ---- helpers ---------------------------------------------------------
    def _alternative(self, action: Action, tried: set[str]) -> Recovery | None:
        try:
            obs = self.c.observe()
        except Exception:  # noqa: BLE001 - observation failure means no alternative
            return None
        if action.type == "extract" and action.mode == "table" and obs.tables:
            words = target_words(action)
            pick = next((t for t in obs.tables if words and any(w in " ".join([t.get("id", "")] + t.get("headers", [])).lower() for w in words)), obs.tables[0])
            sel = f"#{pick['id']}" if pick.get("id") else "table"
            if sel != action.selector and sel not in tried:
                return Recovery(f"use the table found on the page ({sel})", replacement=action.model_copy(update={"selector": sel}))
            return None
        alt = find_alternative(action, obs, tried | ({action.selector} if action.selector else set()))
        if not alt:
            return None
        return Recovery(
            f"re-observed the page and picked {alt.selector!r} ({alt.text or alt.name or alt.href!r}) as the closest match",
            replacement=action.model_copy(update={"selector": alt.selector}),
        )

    def _after_failed_verification(self, action: Action, attempt: int, tried: set[str], url_before: str) -> Recovery | None:
        if action.type == "click":
            if attempt == 1:
                return Recovery("the page may still be updating; wait and verify again", ops=[("wait", {"ms": 800})], reverify_only=True)
            if attempt <= 3:
                ops: list[tuple[str, dict]] = [("back", {})] if url_before and self.c.url != url_before else []
                return Recovery(
                    "the click did not have the expected effect; look for another route",
                    ops=ops,
                    find_after_ops=True,
                )
            return None
        if action.type == "extract":
            return Recovery("content may still be loading; wait and extract again", ops=[("wait", {"ms": 1000})])
        if attempt <= 2:
            return Recovery("retry the action", ops=[("wait", {"ms": 300})])
        return None

    def alternative_route(self, action: Action, tried: set[str]) -> Recovery | None:
        """After a click that did not verify: prefer navigating to a matching link target, else another element."""
        try:
            obs = self.c.observe()
        except Exception:  # noqa: BLE001
            return None
        alt = find_alternative(action, obs, tried | ({action.selector} if action.selector else set()))
        if not alt:
            return None
        if alt.href and not alt.href.startswith(("#", "javascript:")):
            target = urljoin(obs.url, alt.href)
            return Recovery(
                f"navigate directly to the link target {target}",
                replacement=Action(type="navigate", url=target, reason=f"alternative route for: {action.reason}"),
            )
        return Recovery(f"try alternative element {alt.selector!r}", replacement=action.model_copy(update={"selector": alt.selector}))
