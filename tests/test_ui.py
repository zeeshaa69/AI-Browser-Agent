"""Drives the built dashboard in a real browser against a live server."""

import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from browser_agent import server

pytestmark = [pytest.mark.browser, pytest.mark.e2e]

if not any((d / "index.html").is_file() for d in server.UI_DIRS):
    pytest.skip("dashboard not built (cd frontend && npm run build)", allow_module_level=True)

SHOT = Path(__file__).resolve().parents[1] / "docs" / "dashboard.png"


@pytest.fixture()
def live(demo, tmp_path):
    app = server.create_app(runs_dir=tmp_path, demo_url=demo.url)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/"
    srv.should_exit = True
    t.join(timeout=10)


@pytest.fixture()
def page():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 1100})
        yield pg
        b.close()


def test_dashboard_runs_a_task(live, page):
    page.goto(live)
    expect(page.locator("#goal")).to_have_value("Extract the pricing table from the local demo website")
    page.click("#run")
    expect(page.locator("#completion")).to_contain_text("Completed", timeout=60_000)
    expect(page.locator("#status-badge")).to_have_text("completed")
    expect(page.locator("#steps li[data-state=done]")).to_have_count(3)
    expect(page.locator("#history tbody tr")).to_have_count(3)
    rows = page.locator("#data-pricing tbody tr")
    expect(rows).to_have_count(4)
    expect(rows.first).to_contain_text("Starter")
    expect(page.locator("#browser")).to_contain_text("pricing.html")
    expect(page.locator("#shot-main")).to_be_visible()
    assert page.evaluate("document.querySelector('#shot-main').naturalWidth") > 100
    page.click("button[data-format=csv]")
    expect(page.locator("#export-text")).to_contain_text("Plan,Price,Seats,Support")
    SHOT.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(SHOT), full_page=True)


def test_dashboard_confirmation_modal(live, page):
    page.goto(live)
    page.fill("#goal", 'On the local demo site open the contact page and fill the form with name=Ada, message="hi"')
    page.click("#run")
    expect(page.locator("#approve")).to_be_visible(timeout=60_000)
    expect(page.locator(".modal")).to_contain_text("submits data")
    page.click("#deny")
    expect(page.locator("#completion")).to_contain_text("confirmation denied", timeout=30_000)
    expect(page.locator("#errors")).to_contain_text("confirmation denied")


def test_dashboard_shows_planning_errors(live, page):
    page.goto(live)
    page.fill("#goal", "Extract the pricing table")
    page.click("#run")
    expect(page.locator("#completion")).to_contain_text("does not name a website", timeout=30_000)
