"""Command line interface: ``browser-agent run|plan|demo|serve|test``."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__, extraction
from .agent import Agent, AgentConfig
from .demo_site import DemoServer
from .errors import PlanningError
from .planner import build_planner
from .safety import SafetyPolicy, approve_all, interactive_confirmer

_DEMO_GOAL = re.compile(r"\b(local\s+)?demo\s+(web)?site\b|\bdemo\s+(app|page)\b|\blocal\s+test\s+site\b", re.I)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--url", help="start URL (otherwise taken from the goal)")
    p.add_argument("--planner", choices=["rules", "ollama"], default="rules", help="goal planner (default: deterministic rules)")
    p.add_argument("--model", help="local Ollama model name (with --planner ollama)")
    p.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="local model endpoint (must be loopback)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="browser-agent", description="Local, safety-controlled browser agent.")
    ap.add_argument("--version", action="version", version=f"browser-agent {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a goal in a local browser")
    run.add_argument("goal")
    _add_common(run)
    run.add_argument("--allow-domain", action="append", default=[], metavar="DOMAIN", help="allowed domain (repeatable; default: the start URL's host)")
    run.add_argument("--block-domain", action="append", default=[], metavar="DOMAIN", help="blocked domain (repeatable; wins over --allow-domain)")
    run.add_argument("--max-actions", type=int, default=40)
    run.add_argument("--timeout", type=float, default=180.0, help="whole-task timeout in seconds")
    run.add_argument("--action-timeout", type=int, default=10_000, help="per-action timeout in milliseconds")
    run.add_argument("--max-retries", type=int, default=3)
    run.add_argument("--format", choices=["json", "csv", "markdown"], default="json", help="extraction output format")
    run.add_argument("--output", "-o", help="write extracted data to this file instead of stdout")
    run.add_argument("--runs-dir", default="runs", help="where logs and screenshots are stored")
    run.add_argument("--screenshots", choices=["steps", "final", "none"], default="steps")
    run.add_argument("--headed", action="store_true", help="show the browser window")
    run.add_argument("--yes", action="store_true", help="auto-approve actions that need confirmation (never overrides hard blocks)")
    run.add_argument("--no-confirm", action="store_true", help="disable confirmation prompts entirely (sensitive submits are allowed; hard blocks remain)")
    run.add_argument("--json", action="store_true", dest="as_json", help="print the full run result as JSON")

    plan = sub.add_parser("plan", help="print the plan for a goal without opening a browser")
    plan.add_argument("goal")
    _add_common(plan)

    demo = sub.add_parser("demo", help="serve the local demo website")
    demo.add_argument("--port", type=int, default=8765)

    serve = sub.add_parser("serve", help="start the dashboard (API + UI)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--demo-port", type=int, default=8765, help="port for the bundled demo site (0 disables it)")
    serve.add_argument("--runs-dir", default="runs")
    serve.add_argument("--headed", action="store_true")

    test = sub.add_parser("test", help="run the built-in end-to-end self test in a real browser")
    test.add_argument("--pytest", action="store_true", help="run the pytest suite instead (needs the repository checkout)")
    test.add_argument("--only", help="run only scenarios whose name contains this text")
    test.add_argument("--headed", action="store_true")
    return ap


def _planner(args: argparse.Namespace, demo_url: str | None):  # type: ignore[no-untyped-def]
    return build_planner(args.planner, demo_url=demo_url, model=args.model, endpoint=args.ollama_url)


def cmd_plan(args: argparse.Namespace) -> int:
    demo_url = "http://127.0.0.1:8765/" if _DEMO_GOAL.search(args.goal) and not args.url else None
    try:
        plan = _planner(args, demo_url).plan(args.goal, args.url)
    except PlanningError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(plan.model_dump_json(indent=2, exclude_defaults=False))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    demo: DemoServer | None = None
    demo_url = None
    if not args.url and _DEMO_GOAL.search(args.goal):
        demo = DemoServer().start()
        demo_url = demo.url
        print(f"[demo] serving the local demo site at {demo_url}", file=sys.stderr)
    try:
        policy = SafetyPolicy(
            allowed_domains=args.allow_domain, blocked_domains=args.block_domain, max_actions=args.max_actions,
            action_timeout_ms=args.action_timeout, task_timeout_s=args.timeout, require_confirmation=not args.no_confirm,
        )
        try:
            planner = _planner(args, demo_url)
        except PlanningError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        def on_event(ev: dict) -> None:
            if ev["type"] == "log":
                e = ev["entry"]
                err = f" ERROR: {e['error']}" if e["error"] else ""
                print(f"[{e['timestamp'][11:23]}] step {e['step']} {e['action']:<22} {e['result']:<7} {e['target'][:60]}{err}", file=sys.stderr)
            elif ev["type"] == "plan":
                print(f"[plan] {len(ev['plan']['steps'])} steps ({ev['plan']['planner']})", file=sys.stderr)

        cfg = AgentConfig(
            policy=policy, planner=planner, confirmer=approve_all if args.yes else interactive_confirmer, headless=not args.headed,
            runs_dir=args.runs_dir, max_retries=args.max_retries, screenshots=args.screenshots, output_format=args.format,
            demo_url=demo_url, on_event=on_event,
        )
        result = Agent(cfg).run(args.goal, args.url)
    finally:
        if demo:
            demo.stop()

    rendered = result.render(args.format) if result.extracted else ""
    if args.as_json:
        print(result.model_dump_json(indent=2))
    elif rendered:
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
            print(f"[output] wrote {args.output}", file=sys.stderr)
        else:
            print(rendered)
    print(f"[{result.status}] {result.steps_completed}/{result.steps_total} steps, {result.actions_used} actions, {result.duration_s}s; run dir: {result.run_dir}", file=sys.stderr)
    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
    return {"completed": 0, "blocked": 3}.get(result.status, 1)


def cmd_demo(args: argparse.Namespace) -> int:
    srv = DemoServer(port=args.port)
    print(f"Demo site on {srv.url} (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    demo = DemoServer(port=args.demo_port).start() if args.demo_port else None
    app = create_app(runs_dir=args.runs_dir, demo_url=demo.url if demo else None, headless=not args.headed)
    print(f"Dashboard: http://{args.host}:{args.port}/" + (f"   demo site: {demo.url}" if demo else ""))
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        if demo:
            demo.stop()
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    if args.pytest:
        import subprocess

        return subprocess.call([sys.executable, "-m", "pytest", "-q"])
    from .selftest import run_all

    ok = run_all(headless=not args.headed, only=args.only)
    print("self test: " + ("all scenarios passed" if ok else "FAILURES"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {"run": cmd_run, "plan": cmd_plan, "demo": cmd_demo, "serve": cmd_serve, "test": cmd_test}[args.cmd]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
