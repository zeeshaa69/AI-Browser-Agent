import json
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.e2e]


def cli(*args, timeout=120):
    return subprocess.run([sys.executable, "-m", "browser_agent.cli", *args], capture_output=True, text=True, timeout=timeout)


def test_run_against_demo_url_json(demo, tmp_path):
    p = cli("run", "Extract the pricing table", "--url", demo.url, "--runs-dir", str(tmp_path))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["pricing"][0]["Plan"] == "Starter"
    assert "[completed]" in p.stderr


def test_run_demo_goal_starts_demo_server_and_writes_csv(tmp_path):
    out = tmp_path / "pricing.csv"
    p = cli("run", "Extract the pricing table from the local demo website", "--format", "csv", "-o", str(out), "--runs-dir", str(tmp_path))
    assert p.returncode == 0, p.stderr
    assert out.read_text().splitlines()[0] == "Plan,Price,Seats,Support"


def test_run_markdown(demo, tmp_path):
    p = cli("run", "Extract the products table", "--url", demo.url, "--format", "markdown", "--runs-dir", str(tmp_path))
    assert p.returncode == 0 and "| HW-100 | Widget | Hardware | 42 |" in p.stdout


def test_blocked_domain_exit_code(demo, tmp_path):
    p = cli("run", "Extract the pricing table", "--url", demo.url, "--block-domain", "127.0.0.1", "--runs-dir", str(tmp_path))
    assert p.returncode == 3 and "blocked" in p.stderr


def test_unconfirmed_submit_is_blocked_without_yes(demo, tmp_path):
    goal = 'open the contact page and fill the form with name=Ada, message="hi"'
    p = cli("run", goal, "--url", demo.url, "--runs-dir", str(tmp_path))
    assert p.returncode == 3 and "confirmation denied" in p.stderr
    p = cli("run", goal, "--url", demo.url, "--runs-dir", str(tmp_path), "--yes")
    assert p.returncode == 0, p.stderr


def test_goal_without_site_is_usage_error(tmp_path):
    p = cli("run", "Extract the pricing table", "--runs-dir", str(tmp_path))
    assert p.returncode == 1 and "does not name a website" in p.stderr


def test_plan_command_needs_no_browser():
    p = cli("plan", "Extract the pricing table from the local demo website")
    assert p.returncode == 0
    plan = json.loads(p.stdout)
    assert [s["action"]["type"] for s in plan["steps"]] == ["navigate", "click", "extract"]


def test_self_test_subcommand():
    p = cli("test", "--only", "pricing", timeout=180)
    assert p.returncode == 0 and "PASS  pricing_extraction" in p.stdout
