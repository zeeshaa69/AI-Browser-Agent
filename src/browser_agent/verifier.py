"""Post-action verification: did the action actually have the intended effect?"""

from __future__ import annotations

from pathlib import Path

from .actions import Action, Expectation, PlanStep
from .models import ActionOutcome, VerifyResult


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


class Verifier:
    def __init__(self, controller) -> None:  # type: ignore[no-untyped-def]
        self.c = controller

    def verify(self, step: PlanStep, action: Action, outcome: ActionOutcome) -> VerifyResult:
        implicit = self._implicit(action, outcome)
        if not implicit.ok:
            return implicit
        exp = step.expect
        if exp.is_empty():
            return VerifyResult(ok=True, checked=implicit.checked, detail=implicit.detail or "no expectation set for this step")
        result = self._expectation(exp)
        if result.ok and implicit.detail:
            result.detail = f"{implicit.detail}; {result.detail}"
        return result

    # ---- per-action intrinsic checks ------------------------------------
    def _implicit(self, action: Action, outcome: ActionOutcome) -> VerifyResult:
        t = action.type
        if t == "navigate":
            policy_problem = self.c.policy.url_problem(self.c.url)
            if policy_problem:
                return VerifyResult(ok=False, detail=f"ended on a disallowed page: {policy_problem}")
            return VerifyResult(ok=True, detail=f"page loaded at {self.c.url}")
        if t == "type":
            actual = self.c.input_value(action.selector or "")
            if actual != (action.text or ""):
                return VerifyResult(ok=False, detail=f"field value {actual!r} does not match typed text")
            return VerifyResult(ok=True, detail="field value matches typed text")
        if t == "select":
            value, label = self.c.selected_value(action.selector or "")
            if (action.value or "") not in (value, label):
                return VerifyResult(ok=False, detail=f"selected option is {value!r}/{label!r}, expected {action.value!r}")
            return VerifyResult(ok=True, detail=f"option {label!r} is selected")
        if t == "extract":
            data = outcome.data
            if data in (None, "", [], {}):
                return VerifyResult(ok=False, detail="extraction returned no data")
            return VerifyResult(ok=True, detail=outcome.detail)
        if t == "screenshot":
            p = Path(outcome.screenshot or "")
            if not p.is_file() or p.stat().st_size == 0:
                return VerifyResult(ok=False, detail="screenshot file missing or empty")
            return VerifyResult(ok=True, detail=f"screenshot {p.name} written ({p.stat().st_size} bytes)")
        if t == "scroll":
            d = outcome.data or {}
            moved = d.get("y") != d.get("before")
            at_edge = (action.direction in ("down", "bottom", None) and d.get("y", 0) >= d.get("max", 0)) or (
                action.direction in ("up", "top") and d.get("y", 0) <= 0
            )
            if not (moved or at_edge):
                return VerifyResult(ok=False, detail="scroll position did not change")
            return VerifyResult(ok=True, detail="scroll position changed" if moved else "already at the scroll limit")
        return VerifyResult(ok=True, checked=False, detail="")

    # ---- plan expectations -----------------------------------------------
    def _expectation(self, exp: Expectation) -> VerifyResult:
        checks: list[tuple[str, bool]] = []
        if exp.url_contains:
            checks.append((f"url contains {exp.url_contains!r}", _norm(exp.url_contains) in _norm(self.c.url)))
        if exp.title_contains:
            checks.append((f"title contains {exp.title_contains!r}", _norm(exp.title_contains) in _norm(self.c.title)))
        if exp.text_contains:
            checks.append((f"text contains {exp.text_contains!r}", _norm(exp.text_contains) in _norm(self.c.visible_text())))
        if exp.selector_visible:
            checks.append((f"{exp.selector_visible!r} visible", self.c.is_visible(exp.selector_visible)))
        passed = [d for d, ok in checks if ok]
        failed = [d for d, ok in checks if not ok]
        ok = (not failed) if exp.mode == "all" else bool(passed)
        detail = "; ".join(f"ok: {d}" for d in passed) + ("; " if passed and failed else "") + "; ".join(f"FAILED: {d}" for d in failed)
        return VerifyResult(ok=ok, detail=f"{detail} (url={self.c.url})" if not ok else detail)
