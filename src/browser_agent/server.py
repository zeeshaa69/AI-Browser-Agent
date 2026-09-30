"""Dashboard backend: starts tasks, exposes their live state, relays confirmations."""

from __future__ import annotations

import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import extraction
from .agent import Agent, AgentConfig
from .planner import RulesPlanner
from .safety import ConfirmRequest, SafetyPolicy

UI_DIRS = [Path(__file__).parent / "ui_dist"]
CONFIRM_TIMEOUT_S = 300
_SHOT_NAME = re.compile(r"^[\w.-]+\.png$")


class TaskRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=1000)
    start_url: str | None = None
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    max_actions: int = Field(default=40, ge=1, le=200)
    timeout_s: float = Field(default=120, ge=5, le=1800)
    format: str = Field(default="json", pattern="^(json|csv|markdown)$")


class ConfirmBody(BaseModel):
    approve: bool


class TaskRun:
    """Live, thread-safe view of one agent run."""

    def __init__(self, req: TaskRequest):
        self.id = uuid.uuid4().hex[:8]
        self.req = req
        self.lock = threading.Lock()
        self.status = "queued"
        self.phase = "queued"
        self.step: int | None = None
        self.plan: dict | None = None
        self.step_states: dict[int, dict[str, Any]] = {}
        self.browser: dict[str, Any] = {"state": "not started", "url": "", "title": ""}
        self.log: list[dict] = []
        self.screenshots: list[str] = []
        self.extracted: dict[str, Any] = {}
        self.error: str | None = None
        self.pending: dict | None = None
        self.result: dict | None = None
        self.run_dir: Path | None = None
        self.created = time.time()
        self.agent: Agent | None = None
        self._decision = threading.Event()
        self._approved = False

    # ---- agent callbacks -------------------------------------------------
    def on_event(self, ev: dict) -> None:
        kind = ev["type"]
        with self.lock:
            if kind == "status":
                self.status = ev["status"] if ev["status"] in ("completed", "failed", "blocked", "aborted") else "running"
                self.phase = ev.get("phase", self.phase)
                self.step = ev.get("step", self.step)
                if "run_dir" in ev:
                    self.run_dir = Path(ev["run_dir"])
                if ev.get("phase") == "done":
                    self.status, self.error, self.result = ev["status"], ev.get("error"), ev.get("result")
                    self.pending = None
            elif kind == "plan":
                self.plan = ev["plan"]
                self.step_states = {s["id"]: {"state": "pending"} for s in ev["plan"]["steps"]}
            elif kind == "step":
                self.step_states[ev["step"]] = {"state": ev["state"], "error": ev.get("error")}
            elif kind == "observation":
                self.browser.update(url=ev["url"], title=ev["title"])
            elif kind == "browser":
                self.browser["state"] = ev["state"]
            elif kind == "log":
                self.log.append(ev["entry"])
            elif kind == "screenshot":
                name = Path(ev["path"]).name
                if name not in self.screenshots:
                    self.screenshots.append(name)
            elif kind == "extracted":
                self.extracted[ev["name"]] = ev["data"]
            elif kind == "confirm_request":
                self._decision.clear()
                self._approved = False
                self.pending = {"step": ev["step"], "description": ev["description"], "action": ev["action"]}
            elif kind == "confirm_result":
                self.pending = None

    def confirmer(self, req: ConfirmRequest) -> bool:
        return self._decision.wait(CONFIRM_TIMEOUT_S) and self._approved

    def decide(self, approve: bool) -> bool:
        with self.lock:
            if not self.pending:
                return False
        self._approved = approve
        self._decision.set()
        return True

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            steps = []
            for s in (self.plan or {}).get("steps", []):
                st = self.step_states.get(s["id"], {})
                steps.append({"id": s["id"], "description": s["description"], "action": s["action"]["type"], "state": st.get("state", "pending"), "error": st.get("error")})
            finished = self.status in ("completed", "failed", "blocked", "aborted")
            return {
                "id": self.id,
                "goal": self.req.goal,
                "status": "awaiting_confirmation" if self.pending else self.status,
                "phase": self.phase,
                "current_step": self.step,
                "finished": finished,
                "plan_notes": (self.plan or {}).get("notes", []),
                "planner": (self.plan or {}).get("planner"),
                "steps": steps,
                "browser": dict(self.browser),
                "log": self.log[since:],
                "log_total": len(self.log),
                "screenshots": list(self.screenshots),
                "extracted": self.extracted,
                "error": self.error,
                "pending_confirmation": self.pending,
                "result": self.result,
                "format": self.req.format,
            }


def create_app(runs_dir: str | Path = "runs", demo_url: str | None = None, headless: bool = True, **_: Any) -> FastAPI:
    app = FastAPI(title="Local Browser Agent", docs_url="/api/docs", openapi_url="/api/openapi.json")
    tasks: dict[str, TaskRun] = {}
    state_lock = threading.Lock()

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):  # type: ignore[no-untyped-def]
        # The API can drive a browser: refuse cross-site requests from other web pages.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

    def active() -> TaskRun | None:
        return next((t for t in tasks.values() if not t.snapshot()["finished"]), None)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/config")
    def config() -> dict:
        return {"demo_url": demo_url, "default_goal": "Extract the pricing table from the local demo website" if demo_url else ""}

    @app.post("/api/tasks")
    def create_task(req: TaskRequest) -> dict:
        with state_lock:
            if active():
                raise HTTPException(409, "a task is already running")
            run = TaskRun(req)
            tasks[run.id] = run
        policy = SafetyPolicy(
            allowed_domains=req.allowed_domains, blocked_domains=req.blocked_domains, max_actions=req.max_actions,
            task_timeout_s=req.timeout_s,
        )
        cfg = AgentConfig(
            policy=policy, planner=RulesPlanner(demo_url=demo_url), confirmer=run.confirmer, headless=headless,
            runs_dir=runs_dir, output_format=req.format, demo_url=demo_url, on_event=run.on_event,
        )
        run.agent = Agent(cfg)

        def work() -> None:
            try:
                run.agent.run(req.goal, req.start_url or None)  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001 - surface unexpected crashes in the UI
                with run.lock:
                    run.status, run.phase, run.error = "failed", "done", f"internal error: {exc}"

        threading.Thread(target=work, name=f"task-{run.id}", daemon=True).start()
        return {"id": run.id}

    def get(task_id: str) -> TaskRun:
        run = tasks.get(task_id)
        if not run:
            raise HTTPException(404, "unknown task")
        return run

    @app.get("/api/tasks")
    def list_tasks() -> list[dict]:
        return [{"id": t.id, "goal": t.req.goal, "status": t.snapshot()["status"]} for t in sorted(tasks.values(), key=lambda t: -t.created)]

    @app.get("/api/tasks/{task_id}")
    def task_state(task_id: str, since: int = 0) -> dict:
        return get(task_id).snapshot(since)

    @app.post("/api/tasks/{task_id}/confirm")
    def confirm(task_id: str, body: ConfirmBody) -> dict:
        if not get(task_id).decide(body.approve):
            raise HTTPException(409, "no confirmation is pending")
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str) -> dict:
        run = get(task_id)
        if run.agent:
            run.agent.cancel()
        run.decide(False)
        return {"ok": True}

    @app.get("/api/tasks/{task_id}/screenshots/{name}")
    def screenshot(task_id: str, name: str):  # type: ignore[no-untyped-def]
        run = get(task_id)
        if not _SHOT_NAME.match(name) or not run.run_dir:
            raise HTTPException(404)
        path = run.run_dir / "screenshots" / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png")

    @app.get("/api/tasks/{task_id}/export")
    def export(task_id: str, format: str = "json"):  # type: ignore[no-untyped-def]
        run = get(task_id)
        try:
            body = extraction.render(run.extracted, format)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        media = {"json": "application/json", "csv": "text/csv"}.get(format, "text/markdown")
        return PlainTextResponse(body, media_type=media)

    ui = next((d for d in UI_DIRS if (d / "index.html").is_file()), None)
    if ui:
        app.mount("/", StaticFiles(directory=ui, html=True), name="ui")
    else:
        @app.get("/", response_class=HTMLResponse)
        def no_ui() -> str:
            return "<h1>Local Browser Agent</h1><p>The dashboard UI is not built. Run <code>npm install &amp;&amp; npm run build</code> in <code>frontend/</code>. The API is available under <code>/api</code>.</p>"

    app.state.tasks = tasks
    return app
