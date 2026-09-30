import pytest
from pydantic import ValidationError

from browser_agent.actions import Action, Expectation, Plan, PlanStep, parse_action


def step(i, action):
    return PlanStep(id=i, description=f"step {i}", action=action)


def test_valid_click():
    a = parse_action({"type": "click", "selector": "#go", "reason": "press go"})
    assert a.type == "click" and a.target == "#go"


@pytest.mark.parametrize(
    "data",
    [
        {"type": "click", "reason": "no selector"},
        {"type": "click", "selector": "#x"},  # reason missing
        {"type": "click", "selector": "#x", "reason": ""},
        {"type": "type", "selector": "#x", "reason": "no text"},
        {"type": "select", "selector": "#x", "reason": "no value"},
        {"type": "extract", "reason": "no selector"},
        {"type": "wait", "reason": "neither ms nor selector"},
        {"type": "navigate", "url": "file:///etc/passwd", "reason": "local file"},
        {"type": "navigate", "url": "javascript:alert(1)", "reason": "script"},
        {"type": "navigate", "url": "/relative", "reason": "relative"},
        {"type": "navigate", "reason": "no url"},
        {"type": "delete_everything", "reason": "unknown type"},
        {"type": "click", "selector": "#x", "reason": "r", "unexpected": 1},
        {"type": "wait", "ms": 10**9, "reason": "too long"},
        {"type": "scroll", "direction": "sideways", "reason": "bad direction"},
    ],
)
def test_invalid_actions_rejected(data):
    with pytest.raises(ValidationError):
        parse_action(data)


def test_all_supported_types_construct():
    actions = [
        {"type": "navigate", "url": "https://example.com", "reason": "r"},
        {"type": "click", "selector": "a", "reason": "r"},
        {"type": "type", "selector": "input", "text": "hi", "reason": "r"},
        {"type": "select", "selector": "select", "value": "a", "reason": "r"},
        {"type": "scroll", "direction": "down", "reason": "r"},
        {"type": "extract", "selector": "table", "mode": "table", "reason": "r"},
        {"type": "screenshot", "reason": "r"},
        {"type": "wait", "ms": 10, "reason": "r"},
    ]
    assert [parse_action(a).type for a in actions] == [a["type"] for a in actions]


def test_plan_requires_sequential_ids_and_steps():
    a = Action(type="wait", ms=1, reason="r")
    Plan(goal="g", steps=[step(1, a), step(2, a)])
    with pytest.raises(ValidationError):
        Plan(goal="g", steps=[step(1, a), step(3, a)])
    with pytest.raises(ValidationError):
        Plan(goal="g", steps=[])


def test_expectation_empty():
    assert Expectation().is_empty()
    assert not Expectation(url_contains="x").is_empty()
