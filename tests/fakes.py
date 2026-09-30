"""A scripted stand-in for BrowserController so retry/timeout logic can be tested without a browser."""

import time

from browser_agent.errors import ElementNotFound
from browser_agent.models import ActionOutcome, ElementInfo, Observation
from browser_agent.safety import SafetyPolicy


class FakeController:
    def __init__(self, policy: SafetyPolicy, shots_dir, script=None, delay=0.0):
        self.policy = policy
        self.script = list(script or [])  # exceptions (raised) or outcomes (returned), consumed in order
        self.delay = delay
        self.running = False
        self.url = "http://127.0.0.1/"
        self.title = "Fake"
        self.calls: list[tuple[str, int | None]] = []
        self.waits: list[int] = []
        self.observation = Observation(url=self.url, title="Fake", text="", elements=[])

    def start(self): self.running = True
    def stop(self): self.running = False

    def resolve(self, selector, timeout_ms=None):
        return ElementInfo(selector=selector, tag="a", text="link")

    def execute(self, action, timeout_ms=None):
        self.calls.append((action.type, timeout_ms))
        if self.delay:
            time.sleep(self.delay)
        if not self.script:
            return ActionOutcome(detail="ok", data="data")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def wait(self, ms=None, selector=None, timeout_ms=None):
        self.waits.append(ms or 0)
        return ActionOutcome(detail=f"waited {ms}")

    def reload(self): return ActionOutcome(detail="reload")
    def back(self): return ActionOutcome(detail="back")
    def dismiss_candidates(self): return []
    def observe(self, with_screenshot=False, max_elements=80): return self.observation
    def screenshot(self, path=None, full_page=False, name=None): return ActionOutcome(detail="shot", screenshot=None)
    def visible_text(self): return ""
    def is_visible(self, selector): return True
    def input_value(self, selector): return ""
    def selected_value(self, selector): return ("", "")
