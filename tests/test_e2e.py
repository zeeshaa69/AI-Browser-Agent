"""End-to-end: every built-in scenario drives a real Chromium against the local demo site."""

import json

import pytest

from browser_agent import selftest

pytestmark = [pytest.mark.browser, pytest.mark.e2e]


@pytest.fixture()
def ctx(demo, tmp_path):
    return selftest.Ctx(url=demo.url, runs_dir=str(tmp_path))


@pytest.mark.parametrize("scenario", selftest.SCENARIOS, ids=lambda f: f.__name__)
def test_scenario(scenario, ctx):
    assert scenario(ctx)


def test_run_artifacts_are_written(ctx):
    from pathlib import Path

    r = ctx.run("Extract the pricing table from the local demo website")
    run_dir = Path(r.run_dir)
    lines = [json.loads(x) for x in (run_dir / "log.jsonl").read_text().splitlines()]
    assert [e["action"] for e in lines][:3] == ["navigate", "click", "extract"]
    assert all({"timestamp", "action", "target", "result", "error", "retry_count"} <= set(e) for e in lines)
    assert json.loads((run_dir / "extracted.json").read_text())["pricing"][1]["Price"] == "$29 / month"
    assert json.loads((run_dir / "result.json").read_text())["status"] == "completed"
    shots = list((run_dir / "screenshots").glob("*.png"))
    assert shots and all(p.read_bytes()[:4] == b"\x89PNG" for p in shots)


def test_csv_and_markdown_output(ctx):
    r = ctx.run("Extract the pricing table from the local demo website")
    assert r.render("csv").splitlines()[0] == "Plan,Price,Seats,Support"
    assert "| Enterprise | Custom | Unlimited | Dedicated manager |" in r.render("markdown")


def test_verification_failure_is_reported_not_hidden(ctx):
    """A click that succeeds mechanically but does not reach the expected page must not count as done."""
    from browser_agent.actions import Action, Expectation, Plan, PlanStep
    from browser_agent.safety import SafetyPolicy

    plan = Plan(goal="wrong page", steps=[
        PlanStep(id=1, description="open", action=Action(type="navigate", url=ctx.url, reason="start")),
        PlanStep(id=2, description="click Contact expecting pricing", action=Action(type="click", selector='a:has-text("Contact")', reason="r", fallback_text="Contact"),
                 expect=Expectation(url_contains="pricing")),
    ])
    r = ctx.run(plan, policy=SafetyPolicy(action_timeout_ms=1500), max_retries=2)
    assert r.status == "failed"
    failed = [e for e in r.log if e["verified"] is False]
    assert failed and "url contains 'pricing'" in failed[0]["error"]
