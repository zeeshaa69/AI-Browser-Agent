import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from browser_agent.errors import PlanningError
from browser_agent.planner import OllamaPlanner, RulesPlanner, build_planner

DEMO = "http://127.0.0.1:9999/"


def kinds(plan):
    return [s.action.type for s in plan.steps]


def test_pricing_goal():
    plan = RulesPlanner(demo_url=DEMO).plan("Extract the pricing table from the local demo website")
    assert kinds(plan) == ["navigate", "click", "extract"]
    assert plan.steps[0].action.url == DEMO
    assert plan.steps[1].action.fallback_text == "pricing"
    assert plan.steps[2].action.mode == "table" and plan.steps[2].action.name == "pricing"


def test_long_goal_with_url():
    plan = RulesPlanner().plan("Open https://shop.test/, find the pricing page, extract the pricing information and save it as structured JSON")
    assert plan.steps[0].action.url == "https://shop.test/"
    assert kinds(plan) == ["navigate", "click", "extract"]


def test_explicit_start_url_wins():
    plan = RulesPlanner(demo_url=DEMO).plan("Extract the products table", start_url="http://127.0.0.1:1234/")
    assert plan.steps[0].action.url == "http://127.0.0.1:1234/"


def test_form_goal_fills_fields_then_submit():
    plan = RulesPlanner(demo_url=DEMO).plan('On the demo site open the contact page and fill the form with name=Ada, email=ada@x.org, message="Hi there"')
    typed = {s.action.selector: s.action.text for s in plan.steps if s.action.type == "type"}
    assert typed == {'[name="name"]': "Ada", '[name="email"]': "ada@x.org", '[name="message"]': "Hi there"}
    assert plan.steps[-1].action.type == "click"
    assert any("confirmation" in n for n in plan.notes)


def test_select_and_load_more():
    plan = RulesPlanner(demo_url=DEMO).plan("Go to the products page on the demo site, select Hardware from the category dropdown, load more and extract the products table")
    assert kinds(plan) == ["navigate", "click", "select", "click", "wait", "extract"]
    assert plan.steps[2].action.selector == 'select[name="category"]' and plan.steps[2].action.value == "Hardware"


def test_screenshot_and_scroll():
    plan = RulesPlanner(demo_url=DEMO).plan("On the demo website scroll to the bottom and take a screenshot")
    assert kinds(plan) == ["navigate", "scroll", "screenshot"]


def test_no_site_is_an_error_not_a_guess():
    with pytest.raises(PlanningError, match="does not name a website"):
        RulesPlanner().plan("Extract the pricing table")


def test_demo_without_url_is_an_error():
    with pytest.raises(PlanningError, match="no demo URL"):
        RulesPlanner().plan("Extract the pricing table from the local demo website")


def test_empty_goal():
    with pytest.raises(PlanningError):
        RulesPlanner().plan("   ")


def test_open_only_goal_notes_limitation():
    plan = RulesPlanner().plan("Just open https://example.org/ please")
    assert kinds(plan) == ["navigate"] and plan.notes


def test_plans_are_deterministic():
    p = RulesPlanner(demo_url=DEMO)
    g = "Extract the pricing table from the local demo website"
    assert p.plan(g).model_dump() == p.plan(g).model_dump()


# ---- local model planner --------------------------------------------------------

class _Fake(BaseHTTPRequestHandler):
    reply = ""

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"message": {"content": type(self).reply}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def fake_ollama():
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_ollama_planner_rejects_non_loopback():
    with pytest.raises(PlanningError, match="on this machine"):
        OllamaPlanner("m", "https://api.example.com")


def test_ollama_planner_uses_valid_model_plan(fake_ollama):
    srv, url = fake_ollama
    _Fake.reply = json.dumps({"steps": [
        {"description": "open", "action": {"type": "navigate", "url": "http://127.0.0.1:1/", "reason": "start"}},
        {"description": "read", "action": {"type": "extract", "selector": "h1", "reason": "title"}, "expect": {"text_contains": "x"}},
    ]})
    plan = OllamaPlanner("tiny", url).plan("read the heading", None)
    assert plan.planner == "ollama:tiny" and kinds(plan) == ["navigate", "extract"]


def test_ollama_planner_falls_back_on_invalid_plan(fake_ollama):
    srv, url = fake_ollama
    _Fake.reply = json.dumps({"steps": [{"description": "x", "action": {"type": "format_disk", "reason": "no"}}]})
    plan = OllamaPlanner("tiny", url, fallback=RulesPlanner(demo_url=DEMO)).plan("Extract the pricing table from the local demo website")
    assert plan.planner == "rules" and any("used deterministic rules planner" in n for n in plan.notes)


def test_ollama_planner_falls_back_when_unreachable():
    planner = OllamaPlanner("tiny", "http://127.0.0.1:9", fallback=RulesPlanner(demo_url=DEMO), timeout_s=1)
    plan = planner.plan("Extract the pricing table from the local demo website")
    assert plan.planner == "rules" and planner.last_error


def test_build_planner():
    assert build_planner("rules").name == "rules"
    with pytest.raises(PlanningError):
        build_planner("ollama")
    with pytest.raises(PlanningError):
        build_planner("gpt")
