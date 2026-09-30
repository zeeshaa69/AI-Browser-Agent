import pytest

from browser_agent.actions import Action, Expectation, Plan, PlanStep
from browser_agent.agent import Agent, AgentConfig
from browser_agent.errors import ActionTimeout, ElementNotFound, NavigationError
from browser_agent.planner import StaticPlanner
from browser_agent.safety import SafetyPolicy

from .fakes import FakeController


def run(script, *, actions=None, policy=None, max_retries=3, delay=0.0, tmp_path=None, **kw):
    actions = actions or [Action(type="extract", selector="table", mode="table", name="t", reason="r")]
    plan = Plan(goal="g", steps=[PlanStep(id=i, description=f"s{i}", action=a) for i, a in enumerate(actions, 1)])
    holder = {}

    def factory(pol, shots):
        holder["c"] = FakeController(pol, shots, script, delay)
        return holder["c"]

    cfg = AgentConfig(policy=policy or SafetyPolicy(allowed_domains=["127.0.0.1"], action_timeout_ms=1000), planner=StaticPlanner(plan), controller_factory=factory, runs_dir=tmp_path, max_retries=max_retries, screenshots="none", **kw)
    return Agent(cfg).run("g"), holder["c"]


def test_retries_then_succeeds(tmp_path):
    result, ctl = run([ElementNotFound("missing"), ElementNotFound("missing")], tmp_path=tmp_path)
    assert result.status == "completed"
    assert [e["result"] for e in result.log if e["action"] == "extract"] == ["retry", "retry", "ok"]
    final = [e for e in result.log if e["action"] == "extract"][-1]
    assert final["retry_count"] == 2
    assert len(ctl.calls) == 3


def test_gives_up_after_max_retries(tmp_path):
    result, ctl = run([ElementNotFound("missing")] * 10, max_retries=2, tmp_path=tmp_path)
    assert result.status == "failed" and "missing" in result.error
    extracts = [e for e in result.log if e["action"] == "extract"]
    assert [e["result"] for e in extracts] == ["retry", "retry", "failed"]
    assert len(ctl.calls) == 3  # first try + 2 retries
    assert not result.extracted


def test_timeout_retried_with_longer_timeout(tmp_path):
    result, ctl = run([ActionTimeout("slow")], tmp_path=tmp_path)
    assert result.status == "completed"
    timeouts = [t for _, t in ctl.calls]
    assert timeouts[0] == 1000 and timeouts[1] == 2000


def test_timeout_growth_is_capped(tmp_path):
    result, ctl = run([ActionTimeout("slow")] * 10, max_retries=5, tmp_path=tmp_path)
    assert max(t for _, t in ctl.calls) <= 4000


def test_navigation_policy_error_is_not_retried(tmp_path):
    result, ctl = run([NavigationError("navigate: request blocked by the domain policy")] * 3, tmp_path=tmp_path)
    assert result.status == "failed" and len(ctl.calls) == 1


def test_optional_step_failure_is_skipped(tmp_path):
    plan = Plan(goal="g", steps=[
        PlanStep(id=1, description="opt", optional=True, action=Action(type="click", selector="#a", reason="r")),
        PlanStep(id=2, description="req", action=Action(type="wait", ms=1, reason="r")),
    ])
    cfg = AgentConfig(policy=SafetyPolicy(allowed_domains=["127.0.0.1"], action_timeout_ms=500), planner=StaticPlanner(plan),
                      controller_factory=lambda p, s: FakeController(p, s, [ElementNotFound("x")] * 2), runs_dir=tmp_path, max_retries=1, screenshots="none")
    result = Agent(cfg).run("g")
    assert result.status == "completed" and any(e["result"] == "skipped" for e in result.log)


def test_task_timeout_aborts(tmp_path):
    actions = [Action(type="wait", ms=1, reason="r") for _ in range(10)]
    policy = SafetyPolicy(allowed_domains=["127.0.0.1"], action_timeout_ms=1000, task_timeout_s=0.3)
    result, _ = run([], actions=actions, policy=policy, delay=0.2, tmp_path=tmp_path)
    assert result.status == "aborted" and "task timeout" in result.error
    assert 0 < result.steps_completed < 10


def test_max_actions_counts_retries(tmp_path):
    policy = SafetyPolicy(allowed_domains=["127.0.0.1"], action_timeout_ms=1000, max_actions=3)
    result, ctl = run([ElementNotFound("x")] * 10, policy=policy, max_retries=9, tmp_path=tmp_path)
    assert result.status == "aborted" and "maximum of 3 actions" in result.error
    assert result.actions_used == 3


def test_cancel(tmp_path):
    plan = Plan(goal="g", steps=[PlanStep(id=i, description="s", action=Action(type="wait", ms=1, reason="r")) for i in range(1, 6)])
    agent = Agent(AgentConfig(policy=SafetyPolicy(allowed_domains=["127.0.0.1"]), planner=StaticPlanner(plan),
                              controller_factory=lambda p, s: FakeController(p, s), runs_dir=tmp_path, screenshots="none"))
    agent.cancel()
    result = agent.run("g")
    assert result.status == "aborted" and "cancelled" in result.error


def test_planning_failure_never_opens_a_browser(tmp_path):
    opened = []
    cfg = AgentConfig(runs_dir=tmp_path, controller_factory=lambda p, s: opened.append(1))
    result = Agent(cfg).run("Extract the pricing table")  # no site named
    assert result.status == "failed" and "planning failed" in result.error and not opened
