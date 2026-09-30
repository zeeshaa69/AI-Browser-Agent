from pathlib import Path

import pytest

from browser_agent.actions import Action
from browser_agent.errors import ActionTimeout, ElementNotFound, NavigationError, SafetyViolation

pytestmark = pytest.mark.browser


def test_navigate_title_url(controller, demo):
    out = controller.navigate(demo.url)
    assert "loaded" in out.detail
    assert controller.title == "Home · Acme Demo"
    assert controller.url == demo.url


def test_click_navigates(controller, demo):
    controller.navigate(demo.url)
    controller.click('a:has-text("Products")')
    assert controller.url.endswith("/products.html")


def test_type_and_select(controller, demo):
    controller.navigate(demo.url + "contact.html")
    controller.type_text('[name="name"]', "Grace")
    assert controller.input_value('[name="name"]') == "Grace"
    controller.select('[name="topic"]', "support")
    assert controller.selected_value('[name="topic"]')[0] == "support"
    controller.select('[name="topic"]', "Sales")  # by label
    assert controller.selected_value('[name="topic"]')[0] == "sales"


def test_scroll(controller, demo):
    controller.navigate(demo.url + "products.html")
    controller.page.set_viewport_size({"width": 800, "height": 200})
    out = controller.scroll("bottom")
    assert out.data["y"] > 0
    assert controller.scroll("top").data["y"] == 0


def test_extract_table_list_links_text(controller, demo):
    controller.navigate(demo.url + "products.html")
    rows = controller.extract("#products-table", "table").data
    assert rows[0] == {"SKU": "HW-100", "Name": "Widget", "Category": "Hardware", "Stock": "42"} and len(rows) == 4
    links = controller.extract("nav", "links").data
    assert [link["text"] for link in links] == ["Home", "Products", "Pricing", "Contact", "Account"]
    assert controller.extract("h1", "text").data == "Products"
    with pytest.raises(Exception, match="no <table>"):
        controller.extract("h1", "table")


def test_extract_list(controller):
    controller.page.set_content("<ul id=l><li>a</li><li>b</li></ul>")
    assert controller.extract("#l", "list").data == ["a", "b"]


def test_extract_respects_hidden_rows(controller, demo):
    controller.navigate(demo.url + "products.html")
    controller.select("#category", "software")
    rows = controller.extract("#products-table", "table").data
    assert [r["SKU"] for r in rows] == ["SW-100", "SW-200"]


def test_screenshot_is_a_png(controller, demo):
    controller.navigate(demo.url)
    out = controller.screenshot(name="home")
    data = Path(out.screenshot).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 1000


def test_wait_for_dynamic_content(controller, demo):
    controller.navigate(demo.url + "pricing.html")
    assert not controller.is_visible("#pricing-table")
    controller.wait(selector="#pricing-table")
    assert controller.is_visible("#pricing-table")


def test_back_forward_reload(controller, demo):
    controller.navigate(demo.url)
    controller.click('a:has-text("Contact")')
    controller.back()
    assert controller.url == demo.url
    controller.forward()
    assert controller.url.endswith("contact.html")
    controller.reload()
    assert controller.url.endswith("contact.html")


def test_missing_element_is_element_not_found(controller, demo):
    controller.navigate(demo.url)
    with pytest.raises(ElementNotFound):
        controller.resolve("#does-not-exist", timeout_ms=300)


def test_invalid_selector_is_reported(controller, demo):
    controller.navigate(demo.url)
    with pytest.raises(ElementNotFound):
        controller.click("a[[[", timeout_ms=300)


def test_wait_times_out(controller, demo):
    controller.navigate(demo.url)
    with pytest.raises(ActionTimeout):
        controller.wait(selector="#never", timeout_ms=300)


def test_http_error_is_navigation_error(controller, demo):
    with pytest.raises(NavigationError, match="404"):
        controller.navigate(demo.url + "missing.html")


def test_navigation_to_disallowed_domain_refused(controller):
    with pytest.raises(SafetyViolation):
        controller.navigate("http://example.com/")


def test_requests_to_other_domains_are_blocked(controller, demo):
    controller.navigate(demo.url)
    other = demo.url.replace("127.0.0.1", "localhost")  # different host name -> not allowed
    failed = controller.page.evaluate("(u) => fetch(u).then(() => 'ok').catch(() => 'blocked')", other)
    assert failed == "blocked" and other in controller.blocked_requests


def test_dialogs_are_dismissed_and_recorded(controller):
    controller.page.set_content("<button onclick=\"alert('hi')\">x</button>")
    controller.click("button")
    assert controller.dialogs == ["alert: hi"]


def test_popups_are_closed(controller, demo):
    controller.navigate(demo.url)
    controller.page.evaluate("(u) => window.open(u)", demo.url)
    controller.page.wait_for_timeout(300)
    assert controller.popups_closed == 1 and len(controller._context.pages) == 1


def test_observe_reports_elements_and_tables(controller, demo):
    controller.navigate(demo.url + "products.html")
    obs = controller.observe()
    assert obs.title.startswith("Products") and "Widget" in obs.text
    assert any(e.selector == "#category" for e in obs.elements)
    # volatile ids are not used as selectors
    nav = [e for e in obs.elements if e.tag == "a"]
    assert all(not e.selector.startswith("#nav-") for e in nav)
    assert obs.tables and obs.tables[0]["rows"] == 4


def test_execute_dispatches_actions(controller, demo):
    controller.execute(Action(type="navigate", url=demo.url, reason="r"))
    controller.execute(Action(type="click", selector='a:has-text("Contact")', reason="r"))
    out = controller.execute(Action(type="extract", selector="h1", reason="r"))
    assert out.data == "Contact us"
