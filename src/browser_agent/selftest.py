"""Built-in end-to-end scenarios. Each one drives a real Chromium against the local demo site.

Used by ``browser-agent test`` and by the pytest suite, so there is one definition of "works".
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from typing import Callable

from .actions import Action, Expectation, Plan, PlanStep
from .agent import Agent, AgentConfig, RunResult
from .demo_site import DemoServer
from .planner import StaticPlanner
from .safety import SafetyPolicy, approve_all, deny_all


@dataclass
class Ctx:
    url: str
    runs_dir: str
    headless: bool = True

    def run(self, goal: str | Plan, *, confirmer=deny_all, policy: SafetyPolicy | None = None, start_url: str | None = None, **kw) -> RunResult:  # type: ignore[no-untyped-def]
        planner = StaticPlanner(goal) if isinstance(goal, Plan) else None
        policy = policy or SafetyPolicy(action_timeout_ms=2500, task_timeout_s=60)
        cfg = AgentConfig(policy=policy, planner=planner, confirmer=confirmer, demo_url=self.url, runs_dir=self.runs_dir, headless=self.headless, **kw)
        return Agent(cfg).run(goal.goal if isinstance(goal, Plan) else goal, start_url)


def _plan(goal: str, *steps: tuple[str, Action, Expectation | None]) -> Plan:
    return Plan(goal=goal, planner="static", steps=[PlanStep(id=i, description=d, action=a, expect=e or Expectation()) for i, (d, a, e) in enumerate(steps, 1)])


def _step_log(res: RunResult, **match):  # type: ignore[no-untyped-def]
    return [e for e in res.log if all(e.get(k) == v for k, v in match.items())]


# ---- scenarios ---------------------------------------------------------------

def pricing_extraction(c: Ctx) -> str:
    r = c.run("Open a website, find the pricing page, extract the pricing information, and save it as structured JSON on the local demo website")
    assert r.status == "completed", r.error
    rows = r.extracted["pricing"]
    assert [x["Plan"] for x in rows] == ["Starter", "Pro", "Team", "Enterprise"], rows
    assert rows[0]["Price"] == "$9 / month"
    assert r.final_url.endswith("pricing.html")
    return f"{len(rows)} plans extracted from {r.final_url}"


def products_filter_and_load_more(c: Ctx) -> str:
    r = c.run("Go to the products page on the demo site, select Hardware from the category dropdown, load more and extract the products table")
    assert r.status == "completed", r.error
    skus = [x["SKU"] for x in r.extracted["products"]]
    assert skus == ["HW-100", "HW-200", "HW-300"], skus  # only visible (hardware) rows, including the dynamically loaded one
    return f"visible rows after filter + load more: {skus}"


def stale_selector_recovery(c: Ctx) -> str:
    plan = _plan(
        "stale selector",
        ("Open demo", Action(type="navigate", url=c.url, reason="start"), None),
        ("Click pricing with a stale id", Action(type="click", selector="#nav-pricing", fallback_text="Pricing", reason="id changes on every load"), Expectation(url_contains="pricing")),
        ("Extract", Action(type="extract", selector="table", mode="table", name="pricing", reason="read table"), None),
    )
    r = c.run(plan)
    assert r.status == "completed", r.error
    assert any(e["retry_count"] >= 1 or e["result"] == "retry" for e in r.log), "expected at least one retry"
    assert _step_log(r, result="retry"), "the stale selector should have produced a logged retry"
    assert len(r.extracted["pricing"]) == 4
    return "stale #nav-pricing selector recovered via re-observation"


def renamed_link_recovery(c: Ctx) -> str:
    r = c.run("Extract the pricing table from the local demo website", start_url=c.url + "index.html?variant=b")
    assert r.status == "completed", r.error
    assert _step_log(r, result="retry"), "link text changed to 'Plans'; a recovery was expected"
    assert len(r.extracted["pricing"]) == 4
    return "link renamed to 'Plans' was found via its href"


def overlay_recovery(c: Ctx) -> str:
    r = c.run("Extract the pricing table from the local demo website", start_url=c.url + "index.html?promo=1")
    assert r.status == "completed", r.error
    assert any(e["action"] == "recovery:dismiss_overlay" and e["result"] == "ok" for e in r.log), "overlay should have been dismissed"
    return "blocking promo modal dismissed, task completed"


def dynamic_content_and_changing_ids(c: Ctx) -> str:
    plan = _plan(
        "dynamic",
        ("Open the dynamic page", Action(type="navigate", url=c.url + "dynamic.html", reason="start"), None),
        ("Click the button that appears after ~800ms", Action(type="click", selector="#late-button", reason="late element"), Expectation(text_contains="Late button clicked")),
        ("Toggle (id changes on every load)", Action(type="click", selector='button:has-text("Toggle")', reason="toggle"), Expectation(selector_visible="text=on")),
        ("Reveal", Action(type="click", selector="#reveal", reason="reveal"), Expectation(text_contains="marmalade")),
        ("Read the revealed text", Action(type="extract", selector="#late-box", mode="text", name="secret", reason="read"), None),
    )
    r = c.run(plan)
    assert r.status == "completed", r.error
    assert r.extracted["secret"] == "The secret word is: marmalade"
    return "waited for a late button, used a text selector for an element whose id changes, read revealed text"


def purchase_is_blocked(c: Ctx) -> str:
    plan = _plan(
        "buy",
        ("Open pricing", Action(type="navigate", url=c.url + "pricing.html", reason="start"), None),
        ("Buy Starter", Action(type="click", selector='button:has-text("Buy Starter")', reason="buy"), None),
    )
    r = c.run(plan, confirmer=approve_all)
    assert r.status == "blocked", (r.status, r.error)
    assert "financial" in (r.error or "")
    return "financial click blocked even with auto-approval"


def account_deletion_is_blocked(c: Ctx) -> str:
    plan = _plan(
        "delete",
        ("Open account", Action(type="navigate", url=c.url + "account.html", reason="start"), None),
        ("Delete", Action(type="click", selector="#delete-account", reason="delete"), None),
    )
    r = c.run(plan, confirmer=approve_all)
    assert r.status == "blocked" and "account_deletion" in (r.error or ""), (r.status, r.error)
    return "account deletion blocked"


def credentials_are_never_typed(c: Ctx) -> str:
    plan = _plan(
        "password",
        ("Open account", Action(type="navigate", url=c.url + "account.html", reason="start"), None),
        ("Type password", Action(type="type", selector='input[name="password"]', text="hunter2", reason="login"), None),
    )
    r = c.run(plan, confirmer=approve_all)
    assert r.status == "blocked" and "credentials" in (r.error or ""), (r.status, r.error)
    return "typing into a password field blocked"


FORM_GOAL = 'On the local demo site, open the contact page and fill the contact form with name=Ada Lovelace, email=ada@example.com, message="Hello from the agent"'


def form_requires_confirmation(c: Ctx) -> str:
    r = c.run(FORM_GOAL, confirmer=deny_all)
    assert r.status == "blocked" and "confirmation" in (r.error or ""), (r.status, r.error)
    assert r.steps_completed == 5  # navigate, open page, three fields; the submit was refused
    return "submit refused without confirmation"


def form_submits_when_confirmed(c: Ctx) -> str:
    seen = []

    def confirmer(req):  # type: ignore[no-untyped-def]
        seen.append(req.reason)
        return True

    r = c.run(FORM_GOAL, confirmer=confirmer)
    assert r.status == "completed", r.error
    assert seen == ["submits data to a website"], seen
    submit = _step_log(r, step=6, result="ok")
    assert submit and submit[0]["verified"] is True, "submit must be verified by the visible status message"
    return "form submitted after one confirmation"


def domain_restrictions(c: Ctx) -> str:
    plan = _plan("external", ("Go external", Action(type="navigate", url="http://example.com/", reason="leave the site"), None))
    r = c.run(plan, policy=SafetyPolicy(allowed_domains=["127.0.0.1"], action_timeout_ms=2500))
    assert r.status == "blocked" and "not in the allowed list" in (r.error or ""), (r.status, r.error)
    r2 = c.run(plan, policy=SafetyPolicy(allowed_domains=["example.com"], blocked_domains=["example.com"], action_timeout_ms=2500))
    assert r2.status == "blocked" and "blocked" in (r2.error or ""), (r2.status, r2.error)
    return "disallowed and blocked domains refused before any request"


def max_actions_enforced(c: Ctx) -> str:
    r = c.run("Extract the pricing table from the local demo website", policy=SafetyPolicy(max_actions=2, action_timeout_ms=2500))
    assert r.status == "aborted" and "maximum of 2 actions" in (r.error or ""), (r.status, r.error)
    assert r.actions_used == 2
    return "run aborted at the action limit"


def missing_element_fails_honestly(c: Ctx) -> str:
    plan = _plan(
        "missing",
        ("Open demo", Action(type="navigate", url=c.url, reason="start"), None),
        ("Click a link that does not exist", Action(type="click", selector='a:has-text("Zebra crossing")', fallback_text="Zebra crossing", reason="nothing like this exists"), None),
    )
    r = c.run(plan, policy=SafetyPolicy(action_timeout_ms=800, task_timeout_s=60), max_retries=2)
    assert r.status == "failed", r.status
    assert not r.extracted
    assert _step_log(r, result="failed"), "failure must be logged"
    return "nothing fabricated; failure reported after retries"


SCENARIOS: list[Callable[[Ctx], str]] = [
    pricing_extraction,
    products_filter_and_load_more,
    stale_selector_recovery,
    renamed_link_recovery,
    overlay_recovery,
    dynamic_content_and_changing_ids,
    purchase_is_blocked,
    account_deletion_is_blocked,
    credentials_are_never_typed,
    form_requires_confirmation,
    form_submits_when_confirmed,
    domain_restrictions,
    max_actions_enforced,
    missing_element_fails_honestly,
]


def run_all(headless: bool = True, runs_dir: str | None = None, only: str | None = None, echo: Callable[[str], None] = print) -> bool:
    ok = True
    with DemoServer() as demo, tempfile.TemporaryDirectory() as tmp:
        ctx = Ctx(url=demo.url, runs_dir=runs_dir or tmp, headless=headless)
        for fn in SCENARIOS:
            if only and only not in fn.__name__:
                continue
            try:
                detail = fn(ctx)
                echo(f"PASS  {fn.__name__}: {detail}")
            except Exception as exc:  # noqa: BLE001 - report every failure
                ok = False
                echo(f"FAIL  {fn.__name__}: {exc.__class__.__name__}: {exc}")
    return ok
