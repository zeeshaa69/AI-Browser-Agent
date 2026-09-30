import pytest

from browser_agent.controller import BrowserController
from browser_agent.demo_site import DemoServer
from browser_agent.safety import SafetyPolicy


@pytest.fixture(scope="session")
def demo():
    with DemoServer() as srv:
        yield srv


@pytest.fixture()
def policy():
    return SafetyPolicy(allowed_domains=["127.0.0.1"], action_timeout_ms=2500, task_timeout_s=60)


@pytest.fixture()
def controller(policy, tmp_path):
    with BrowserController(policy, artifacts_dir=tmp_path / "shots") as c:
        yield c
