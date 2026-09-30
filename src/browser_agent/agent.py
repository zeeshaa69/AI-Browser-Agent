"""The agent loop: plan -> (observe -> guard -> act -> verify -> recover)* -> complete."""

from __future__ import annotations

import copy
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from . import extraction
from .actions import Action, Plan, PlanStep
from .controller import BrowserController
from .errors import (
    AgentError,
    Cancelled,
    ConfirmationDenied,
    ControllerError,
    ErrorKind,
    LimitExceeded,
    PlanningError,
    SafetyViolation,
    VerificationFailed,
)
from .logs import ExecutionLog, LogEntry
from .planner import Planner, RulesPlanner
from .recovery import RecoveryPlanner
from .safety import ConfirmRequest, Confirmer, SafetyPolicy, Verdict, deny_all, host_of
from .verifier import Verifier

Status = Literal["completed", "failed", "blocked", "aborted"]
TIMEOUT_CAP_FACTOR = 4


@dataclass
class AgentConfig:
    policy: SafetyPolicy = field(default_factory=SafetyPolicy)
    planner: Planner | None = None
    confirmer: Confirmer = deny_all
    headless: bool = True
    runs_dir: Path | str = "runs"
    max_retries: int = 3
    screenshots: Literal["steps", "final", "none"] = "steps"
    output_format: str = "json"
    demo_url: str | None = None
    executable_path: str | None = None
    deterministic: bool = True
    on_event: Callable[[dict[str, Any]], None] | None = None
    controller_factory: Callable[[SafetyPolicy, Path], Any] | None = None


class RunResult(BaseModel):
    run_id: str
    goal: str
    status: Status
    error: str | None = None
    plan: Plan | None = None
    steps_total: int = 0
    steps_completed: int = 0
    actions_used: int = 0
    duration_s: float = 0.0
    extracted: dict[str, Any] = Field(default_factory=dict)
    final_url: str = ""
    run_dir: str = ""
    screenshots: list[str] = Field(default_factory=list)
    output_files: list[str] = Field(default_factory=list)
    log: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def render(self, fmt: str | None = None) -> str:
        return extraction.render(self.extracted, fmt or "json")


class _StepFailed(Exception):
    def __init__(self, error: str, status: Status = "failed"):
        super().__init__(error)
        self.status = status


class Agent:
    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        self._cancel = threading.Event()
        self._deadline = 0.0
        self._actions = 0
        self._log: ExecutionLog | None = None
        self._controller: Any = None
        self._extracted: dict[str, Any] = {}
        self._screenshots: list[str] = []
        self.run_id = uuid.uuid4().hex[:8]

    # ---- public ----------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()

    def run(self, goal: str, start_url: str | None = None) -> RunResult:
        cfg = self.config
        started = time.monotonic()
        self._deadline = started + cfg.policy.task_timeout_s
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_dir = Path(cfg.runs_dir) / f"{stamp}-{self.run_id}"
        shots_dir = run_dir / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        self._log = ExecutionLog(run_dir / "log.jsonl", on_entry=lambda e: self._emit("log", entry=e.model_dump()))
        self._extracted, self._screenshots, self._actions = {}, [], 0
        policy = copy.deepcopy(cfg.policy)
        plan: Plan | None = None
        status: Status = "failed"
        error: str | None = None
        steps_done = 0
        final_url = ""
        self._emit("status", status="planning", phase="planning", run_id=self.run_id, goal=goal, run_dir=str(run_dir))
        try:
            planner = cfg.planner or RulesPlanner(demo_url=cfg.demo_url)
            plan = planner.plan(goal, start_url)
            self._emit("plan", plan=plan.model_dump())
            self._prepare_policy(policy, plan)
            factory = cfg.controller_factory or self._default_factory
            self._controller = factory(policy, shots_dir)
            self._controller.start()
            self._emit("browser", state="running")
            self._recovery = RecoveryPlanner(self._controller)
            self._verifier = Verifier(self._controller)
            self._policy = policy
            for step in plan.steps:
                try:
                    self._run_step(step)
                    steps_done += 1
                except _StepFailed as exc:
                    if step.optional and exc.status == "failed":
                        self._log.add(step=step.id, action=step.action.type, target=step.action.target, result="skipped", error=str(exc), detail="optional step skipped")
                        continue
                    status, error = exc.status, f"step {step.id} ({step.description}): {exc}"
                    break
            else:
                status = "completed"
        except PlanningError as exc:
            status, error = "failed", f"planning failed: {exc}"
        except SafetyViolation as exc:
            status, error = "blocked", str(exc)
        except (LimitExceeded, Cancelled) as exc:
            status, error = "aborted", str(exc)
        except ControllerError as exc:
            status, error = "failed", str(exc)
        finally:
            final_url = self._finish_browser(status, shots_dir)

        duration = round(time.monotonic() - started, 2)
        result = RunResult(
            run_id=self.run_id,
            goal=goal,
            status=status,
            error=error,
            plan=plan,
            steps_total=len(plan.steps) if plan else 0,
            steps_completed=steps_done,
            actions_used=self._actions,
            duration_s=duration,
            extracted=self._extracted,
            final_url=final_url,
            run_dir=str(run_dir),
            screenshots=self._screenshots,
            log=self._log.as_dicts(),
        )
        result.output_files = self._write_outputs(run_dir, result)
        self._emit("status", status=status, phase="done", error=error, result=result.model_dump(exclude={"log", "plan"}))
        return result

    # ---- setup -------------------------------------------------------------
    def _default_factory(self, policy: SafetyPolicy, shots_dir: Path) -> BrowserController:
        return BrowserController(policy, headless=self.config.headless, executable_path=self.config.executable_path, artifacts_dir=shots_dir)

    def _prepare_policy(self, policy: SafetyPolicy, plan: Plan) -> None:
        start = plan.start_url()
        if start and not policy.allowed_domains:
            policy.allow_domain(host_of(start))
        if start:
            policy.check_url(start)

    def _finish_browser(self, status: Status, shots_dir: Path) -> str:
        ctl = self._controller
        final_url = ""
        if ctl and getattr(ctl, "running", False):
            try:
                final_url = ctl.url
                if self.config.screenshots != "none":
                    path = ctl.screenshot(name=f"final-{status}").screenshot
                    if path:
                        self._screenshots.append(path)
                        self._emit("screenshot", path=path)
            except Exception:  # noqa: BLE001 - never mask the real result
                pass
            try:
                ctl.stop()
            except Exception:  # noqa: BLE001
                pass
            self._emit("browser", state="closed")
        self._controller = None
        return final_url

    def _write_outputs(self, run_dir: Path, result: RunResult) -> list[str]:
        files = []
        (run_dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        files.append(str(run_dir / "result.json"))
        files.append(str(run_dir / "log.jsonl"))
        if result.extracted:
            fmt = self.config.output_format
            path = run_dir / f"extracted.{extraction.extension_for(fmt)}"
            path.write_text(extraction.render(result.extracted, fmt), encoding="utf-8")
            files.append(str(path))
        return files

    # ---- limits ------------------------------------------------------------
    def _check_limits(self) -> None:
        if self._cancel.is_set():
            raise Cancelled("task cancelled")
        if time.monotonic() > self._deadline:
            raise LimitExceeded(f"task timeout of {self.config.policy.task_timeout_s:g}s exceeded")

    def _count_action(self) -> None:
        if self._actions >= self._policy.max_actions:
            raise LimitExceeded(f"maximum of {self._policy.max_actions} actions reached")
        self._actions += 1

    # ---- events ------------------------------------------------------------
    def _emit(self, kind: str, **data: Any) -> None:
        if self.config.on_event:
            try:
                self.config.on_event({"type": kind, **data})
            except Exception:  # noqa: BLE001 - UI callbacks must not break the run
                pass

    # ---- one step ------------------------------------------------------------
    def _guard(self, action: Action, timeout_ms: int, step: PlanStep):  # type: ignore[no-untyped-def]
        """Resolve the target element, apply the safety policy, ask for confirmation if needed."""
        ctl = self._controller
        element = ctl.resolve(action.selector, timeout_ms) if action.type in ("click", "type", "select", "extract") and action.selector else None
        decision = self._policy.assess(action, element)
        if decision.verdict == Verdict.DENY:
            raise SafetyViolation(f"blocked by safety policy ({decision.category}): {decision.reason}")
        if decision.verdict == Verdict.CONFIRM:
            req = ConfirmRequest(action=action, reason=decision.reason, element=element, url=ctl.url)
            self._emit("confirm_request", step=step.id, description=req.describe(), action=action.model_dump(exclude_none=True))
            waited = time.monotonic()
            approved = self.config.confirmer(req)
            self._deadline += time.monotonic() - waited  # waiting for a person does not eat the task budget
            self._emit("confirm_result", step=step.id, approved=approved)
            self._log.add(
                step=step.id, action=action.type, target=action.target, result="info" if approved else "blocked",
                detail=f"confirmation {'granted' if approved else 'denied'}: {decision.reason}",
                error=None if approved else "confirmation denied",
            )
            if not approved:
                raise ConfirmationDenied(f"confirmation denied for sensitive action ({decision.reason})")
        return element

    def _run_step(self, step: PlanStep) -> None:
        ctl, log = self._controller, self._log
        action = step.action
        self._emit("step", step=step.id, description=step.description, state="running")
        tried: set[str] = set()
        url_before = ctl.url
        timeout_ms = self._policy.action_timeout_ms
        cap = timeout_ms * TIMEOUT_CAP_FACTOR
        retry = 0
        reverify_only = False
        outcome = None
        while True:
            started = time.monotonic()
            err: AgentError
            try:
                self._check_limits()
                if not reverify_only:
                    self._emit("status", status="running", phase="observing", step=step.id)
                    self._emit("observation", step=step.id, url=ctl.url, title=ctl.title)
                    self._emit("status", status="running", phase="acting", step=step.id)
                    self._guard(action, timeout_ms, step)
                    self._count_action()
                    outcome = ctl.execute(action, timeout_ms)
                self._emit("status", status="running", phase="verifying", step=step.id)
                verdict = self._verifier.verify(step, action, outcome)
                duration = int((time.monotonic() - started) * 1000)
                if verdict.ok:
                    shot = outcome.screenshot
                    if action.type != "screenshot" and self.config.screenshots == "steps":
                        shot = ctl.screenshot(name=f"step{step.id}-{action.type}").screenshot
                    if shot:
                        self._screenshots.append(shot)
                        self._emit("screenshot", path=shot, step=step.id)
                    if action.type == "extract":
                        self._store_extraction(action, outcome.data)
                    log.add(
                        step=step.id, action=action.type, target=action.target, result="ok", retry_count=retry,
                        duration_ms=duration, verified=verdict.checked or None, detail=f"{outcome.detail}; {verdict.detail}".strip("; "),
                        screenshot=shot,
                    )
                    self._emit("step", step=step.id, description=step.description, state="done")
                    return
                err = VerificationFailed(verdict.detail)
            except SafetyViolation as exc:
                log.add(step=step.id, action=action.type, target=action.target, result="blocked", error=str(exc), retry_count=retry)
                self._emit("step", step=step.id, description=step.description, state="blocked", error=str(exc))
                raise _StepFailed(str(exc), "blocked") from exc
            except (LimitExceeded, Cancelled):
                log.add(step=step.id, action=action.type, target=action.target, result="failed", error="run aborted (limit or cancellation)", retry_count=retry)
                raise
            except ControllerError as exc:
                err = exc

            duration = int((time.monotonic() - started) * 1000)
            retry += 1
            self._emit("status", status="running", phase="recovering", step=step.id)
            rec = self._recovery.propose(action, err.kind, str(err), retry, tried=tried, url_before=url_before, max_attempts=self.config.max_retries)
            log.add(
                step=step.id, action=action.type, target=action.target, result="retry" if rec else "failed",
                error=str(err), retry_count=retry - 1, duration_ms=duration, verified=False if err.kind == ErrorKind.VERIFICATION else None,
                detail=f"recovery: {rec.description}" if rec else "no recovery available",
            )
            if rec is None:
                self._emit("step", step=step.id, description=step.description, state="failed", error=str(err))
                raise _StepFailed(str(err))
            if action.selector:
                tried.add(action.selector)
            self._run_recovery_ops(step, rec.ops)
            if rec.find_after_ops:
                alt = self._recovery.alternative_route(action, tried)
                if alt is None:
                    log.add(step=step.id, action=action.type, target=action.target, result="failed", error="no alternative route found", retry_count=retry)
                    self._emit("step", step=step.id, description=step.description, state="failed", error=str(err))
                    raise _StepFailed(f"{err} (no alternative route found)")
                log.add(step=step.id, action="recovery:alternative", target=alt.description, result="info", detail=alt.description, retry_count=retry)
                rec = alt
            if rec.replacement is not None:
                action = rec.replacement
                reverify_only = False
            else:
                reverify_only = rec.reverify_only and outcome is not None
            timeout_ms = min(int(timeout_ms * rec.timeout_factor), cap)

    def _run_recovery_ops(self, step: PlanStep, ops: list[tuple[str, dict]]) -> None:
        ctl = self._controller
        for name, params in ops:
            self._check_limits()
            self._count_action()
            started = time.monotonic()
            try:
                if name == "wait":
                    detail = ctl.wait(ms=params.get("ms", 500)).detail
                elif name == "reload":
                    detail = ctl.reload().detail
                elif name == "back":
                    detail = ctl.back().detail
                elif name == "dismiss_overlay":
                    detail = self._dismiss_overlay(step)
                else:  # pragma: no cover - defensive
                    raise ControllerError(f"unknown recovery op {name}")
                result, err = "ok", None
            except ControllerError as exc:
                detail, result, err = "", "failed", str(exc)
            self._log.add(
                step=step.id, action=f"recovery:{name}", target=str(params) if params else "", result=result, error=err,
                duration_ms=int((time.monotonic() - started) * 1000), detail=detail,
            )

    def _dismiss_overlay(self, step: PlanStep) -> str:
        ctl = self._controller
        for selector in ctl.dismiss_candidates():
            click = Action(type="click", selector=selector, reason="dismiss overlay blocking the page")
            element = ctl.resolve(selector, 1500)
            if self._policy.assess(click, element).verdict != Verdict.ALLOW:
                continue  # never auto-click anything sensitive while recovering
            ctl.click(selector, 3000)
            return f"dismissed overlay via {element.text!r}"
        raise ControllerError("no dismissable overlay found")

    def _store_extraction(self, action: Action, data: Any) -> None:
        name = action.name or "data"
        key, n = name, 2
        while key in self._extracted:
            key, n = f"{name}_{n}", n + 1
        self._extracted[key] = data
        self._emit("extracted", name=key, data=data)
