import pytest

from browser_agent.actions import Action
from browser_agent.errors import SafetyViolation
from browser_agent.models import ElementInfo
from browser_agent.safety import ConfirmRequest, SafetyPolicy, Verdict, approve_all, deny_all, domain_matches, normalize_domain


def pol(**kw):
    return SafetyPolicy(allowed_domains=["example.com"], **kw)


def test_allowed_domain_and_subdomain():
    p = pol()
    assert p.url_allowed("https://example.com/a")
    assert p.url_allowed("https://docs.example.com/a")
    assert not p.url_allowed("https://evilexample.com/")
    assert not p.url_allowed("https://example.com.evil.org/")


def test_blocked_wins_over_allowed():
    p = SafetyPolicy(allowed_domains=["example.com"], blocked_domains=["admin.example.com"])
    assert p.url_allowed("https://www.example.com/")
    assert not p.url_allowed("https://admin.example.com/")


def test_empty_allow_list_denies_everything():
    assert not SafetyPolicy().url_allowed("https://example.com/")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "data:text/html,hi", "about:blank"])
def test_non_http_schemes_denied(url):
    assert not pol().url_allowed(url)


def test_check_url_raises():
    with pytest.raises(SafetyViolation):
        pol().check_url("https://other.org/")


def test_domain_normalisation():
    assert normalize_domain("https://Example.com/path") == "example.com"
    assert normalize_domain("*.example.com") == "example.com"
    assert normalize_domain("Example.com:8080") == "example.com"
    assert domain_matches("a.b.example.com", "example.com")


def click(selector="#x"):
    return Action(type="click", selector=selector, reason="r")


@pytest.mark.parametrize("text", ["Buy now", "Pay now", "Place order", "Checkout", "Purchase", "Donate", "Add to cart"])
def test_financial_clicks_denied(text):
    d = pol().assess(click(), ElementInfo(tag="button", text=text))
    assert d.verdict == Verdict.DENY and d.category == "financial"


@pytest.mark.parametrize("text", ["Delete account", "Delete my account", "Close account", "Deactivate your account"])
def test_account_deletion_denied(text):
    d = pol().assess(click(), ElementInfo(tag="button", text=text))
    assert d.verdict == Verdict.DENY and d.category == "account_deletion"


def test_deletion_detected_from_href():
    d = pol().assess(click(), ElementInfo(tag="a", text="Continue", href="/settings/delete-account"))
    assert d.verdict == Verdict.DENY


def test_credentials_never_typed_or_extracted():
    pw = ElementInfo(tag="input", type="password", name="p")
    assert pol().assess(Action(type="type", selector="#p", text="x", reason="r"), pw).verdict == Verdict.DENY
    assert pol().assess(Action(type="extract", selector="#p", reason="r"), pw).verdict == Verdict.DENY
    card = ElementInfo(tag="input", type="text", name="card-number", autocomplete="cc-number")
    assert pol().assess(Action(type="type", selector="#c", text="4111", reason="r"), card).category == "credentials"


def test_normal_fields_allowed():
    email = ElementInfo(tag="input", type="email", name="email", autocomplete="email")
    assert pol().assess(Action(type="type", selector="#e", text="a@b.c", reason="r"), email).verdict == Verdict.ALLOW


def test_submit_requires_confirmation_unless_disabled():
    btn = ElementInfo(tag="button", type="submit", text="Send message", in_form=True)
    assert pol().assess(click(), btn).verdict == Verdict.CONFIRM
    assert pol(require_confirmation=False).assess(click(), btn).verdict == Verdict.ALLOW


def test_enter_in_form_requires_confirmation():
    field = ElementInfo(tag="input", type="text", name="q", in_form=True)
    act = Action(type="type", selector="#q", text="x", press_enter=True, reason="r")
    assert pol().assess(act, field).verdict == Verdict.CONFIRM


def test_plain_link_allowed():
    assert pol().assess(click(), ElementInfo(tag="a", text="Pricing", href="pricing.html")).verdict == Verdict.ALLOW


def test_navigate_assessed_by_domain():
    ok = Action(type="navigate", url="https://example.com/", reason="r")
    bad = Action(type="navigate", url="https://other.org/", reason="r")
    assert pol().assess(ok).verdict == Verdict.ALLOW
    assert pol().assess(bad).verdict == Verdict.DENY


def test_confirmers():
    req = ConfirmRequest(action=click(), reason="r")
    assert approve_all(req) is True
    assert deny_all(req) is False
