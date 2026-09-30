"""Playwright-backed browser controller.

The controller performs raw browser operations and translates Playwright
failures into the agent's error kinds. It makes no safety decisions on its own
except one: every network request to a domain that the policy does not allow is
aborted.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Playwright, TimeoutError as PlaywrightTimeout, sync_playwright

from .actions import Action
from .errors import ActionTimeout, ControllerError, ElementIntercepted, ElementNotFound, NavigationError
from .extraction import EXTRACT_JS, normalize_payload
from .models import ActionOutcome, ElementInfo, Observation
from .observer import COLLECT_JS, DISMISS_JS, observation_from_raw
from .safety import SafetyPolicy

_DESCRIBE_JS = r"""
(el) => {
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const tag = el.tagName.toLowerCase();
  return {
    selector: '', tag,
    role: el.getAttribute('role') || '',
    text: clean(el.innerText || el.value || '').slice(0, 200),
    type: (el.getAttribute('type') || (tag === 'button' ? 'submit' : '')).toLowerCase(),
    name: el.getAttribute('name') || '', id: el.id || '',
    href: el.getAttribute('href') || '',
    aria_label: el.getAttribute('aria-label') || '',
    placeholder: el.getAttribute('placeholder') || '',
    autocomplete: el.getAttribute('autocomplete') || '',
    form_action: el.form ? (el.form.getAttribute('action') || '') : '',
    in_form: !!el.form,
    visible: true, enabled: !el.disabled,
  };
}
"""

_SCROLL_STATE_JS = "() => ({ y: Math.round(window.scrollY), max: Math.max(0, document.documentElement.scrollHeight - window.innerHeight) })"


class BrowserController:
    def __init__(
        self,
        policy: SafetyPolicy,
        *,
        headless: bool = True,
        viewport: tuple[int, int] = (1280, 800),
        executable_path: str | None = None,
        artifacts_dir: Path | str | None = None,
    ) -> None:
        self.policy = policy
        self.headless = headless
        self.viewport = viewport
        self.executable_path = executable_path or os.environ.get("BROWSER_AGENT_CHROMIUM") or None
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        self.blocked_requests: list[str] = []
        self.dialogs: list[str] = []
        self.popups_closed = 0
        self._pw: Playwright | None = None
        self._browser = None
        self._context = None
        self._page: Page | None = None
        self._shots = 0

    # ---- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if self._page:
            return
        self._pw = sync_playwright().start()
        try:
            kwargs: dict[str, Any] = {"headless": self.headless}
            if self.executable_path:
                kwargs["executable_path"] = self.executable_path
            self._browser = self._pw.chromium.launch(**kwargs)
        except PlaywrightError as exc:
            self._pw.stop()
            self._pw = None
            raise ControllerError(
                "Could not launch Chromium. Run `playwright install chromium` "
                f"(or set BROWSER_AGENT_CHROMIUM). Details: {str(exc).splitlines()[0]}"
            ) from exc
        self._context = self._browser.new_context(
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            accept_downloads=False,
            service_workers="block",
            locale="en-US",
            timezone_id="UTC",
            reduced_motion="reduce",
        )
        self._context.route("**/*", self._route)
        self._page = self._context.new_page()
        self._context.on("page", self._on_new_page)
        self._page.on("dialog", self._on_dialog)
        self._page.set_default_timeout(self.policy.action_timeout_ms)

    def stop(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except PlaywrightError:
                pass
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._context = self._page = None

    def __enter__(self) -> "BrowserController":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def running(self) -> bool:
        return self._page is not None

    @property
    def page(self) -> Page:
        if not self._page:
            raise ControllerError("browser is not started")
        return self._page

    # ---- event hooks ---------------------------------------------------
    def _route(self, route) -> None:  # type: ignore[no-untyped-def]
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:")) or self.policy.url_allowed(url):
            route.continue_()
        else:
            self.blocked_requests.append(url)
            route.abort("blockedbyclient")

    def _on_dialog(self, dialog) -> None:  # type: ignore[no-untyped-def]
        self.dialogs.append(f"{dialog.type}: {dialog.message}")
        dialog.dismiss()

    def _on_new_page(self, page: Page) -> None:
        if page != self._page:
            self.popups_closed += 1
            try:
                page.close()
            except PlaywrightError:
                pass

    # ---- state ---------------------------------------------------------
    @property
    def url(self) -> str:
        return self.page.url

    @property
    def title(self) -> str:
        try:
            return self.page.title()
        except PlaywrightError:
            return ""

    # ---- helpers -------------------------------------------------------
    def _locator(self, selector: str):  # type: ignore[no-untyped-def]
        return self.page.locator(selector).first

    def _wrap(self, exc: Exception, what: str, selector: str | None = None) -> ControllerError:
        msg = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        full = str(exc)
        if "intercepts pointer events" in full:
            return ElementIntercepted(f"{what}: another element intercepts pointer events ({selector})")
        if isinstance(exc, PlaywrightTimeout):
            return ActionTimeout(f"{what}: timed out ({msg})")
        if re.search(r"selector|parsing|Unexpected token", full, re.I) and selector:
            return ElementNotFound(f"{what}: invalid or unresolvable selector {selector!r}")
        if "ERR_BLOCKED_BY_CLIENT" in full:
            return NavigationError(f"{what}: request blocked by the domain policy")
        if "net::ERR" in full:
            return NavigationError(f"{what}: {msg}")
        return ControllerError(f"{what}: {msg}")

    def resolve(self, selector: str, timeout_ms: int | None = None) -> ElementInfo:
        """Wait for ``selector`` to be visible and describe it (for safety checks)."""
        timeout = timeout_ms or self.policy.action_timeout_ms
        loc = self._locator(selector)
        try:
            loc.wait_for(state="visible", timeout=timeout)
            info = ElementInfo(**loc.evaluate(_DESCRIBE_JS))
        except PlaywrightTimeout as exc:
            raise ElementNotFound(f"element not found or not visible within {timeout}ms: {selector}") from exc
        except PlaywrightError as exc:
            raise self._wrap(exc, "resolve", selector) from exc
        info.selector = selector
        return info

    def count(self, selector: str) -> int:
        try:
            return self.page.locator(selector).count()
        except PlaywrightError:
            return 0

    # ---- operations ----------------------------------------------------
    def navigate(self, url: str, timeout_ms: int | None = None) -> ActionOutcome:
        self.policy.check_url(url)
        try:
            resp = self.page.goto(url, wait_until="load", timeout=timeout_ms or self.policy.action_timeout_ms)
        except PlaywrightError as exc:
            raise self._wrap(exc, f"navigate {url}") from exc
        if resp is not None and resp.status >= 400:
            raise NavigationError(f"navigate {url}: HTTP {resp.status}")
        self.policy.check_url(self.url)
        return ActionOutcome(detail=f"loaded {self.url} (HTTP {resp.status if resp else '?'})")

    def click(self, selector: str, timeout_ms: int | None = None) -> ActionOutcome:
        timeout = timeout_ms or self.policy.action_timeout_ms
        before = self.url
        try:
            self._locator(selector).click(timeout=timeout)
            self.page.wait_for_load_state("load", timeout=timeout)
        except PlaywrightError as exc:
            raise self._wrap(exc, f"click {selector}", selector) from exc
        return ActionOutcome(detail=f"clicked {selector}" + (f"; navigated to {self.url}" if self.url != before else ""))

    def type_text(self, selector: str, text: str, press_enter: bool = False, timeout_ms: int | None = None) -> ActionOutcome:
        timeout = timeout_ms or self.policy.action_timeout_ms
        try:
            loc = self._locator(selector)
            loc.fill(text, timeout=timeout)
            if press_enter:
                loc.press("Enter", timeout=timeout)
        except PlaywrightError as exc:
            raise self._wrap(exc, f"type into {selector}", selector) from exc
        return ActionOutcome(detail=f"typed {len(text)} characters into {selector}")

    def select(self, selector: str, value: str, timeout_ms: int | None = None) -> ActionOutcome:
        timeout = timeout_ms or self.policy.action_timeout_ms
        loc = self._locator(selector)
        try:
            try:
                loc.select_option(value=value, timeout=timeout // 2 or 1)
            except PlaywrightTimeout:
                loc.select_option(label=value, timeout=timeout // 2 or 1)
        except PlaywrightError as exc:
            raise self._wrap(exc, f"select {value!r} in {selector}", selector) from exc
        return ActionOutcome(detail=f"selected {value!r} in {selector}")

    def selected_value(self, selector: str) -> tuple[str, str]:
        """Returns (value, label) of the selected option."""
        res = self._locator(selector).evaluate("(el) => [el.value, el.selectedOptions[0] ? el.selectedOptions[0].label : '']")
        return res[0], res[1]

    def input_value(self, selector: str) -> str:
        return self._locator(selector).input_value(timeout=self.policy.action_timeout_ms)

    def scroll(self, direction: str = "down", amount: int | None = None) -> ActionOutcome:
        try:
            before = self.page.evaluate(_SCROLL_STATE_JS)
            if direction == "top":
                self.page.evaluate("window.scrollTo(0, 0)")
            elif direction == "bottom":
                self.page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            else:
                delta = (amount or self.viewport[1] * 0.8) * (1 if direction == "down" else -1)
                self.page.evaluate("(d) => window.scrollBy(0, d)", delta)
            after = self.page.evaluate(_SCROLL_STATE_JS)
        except PlaywrightError as exc:
            raise self._wrap(exc, "scroll") from exc
        return ActionOutcome(detail=f"scrolled {direction}: y {before['y']} -> {after['y']} (max {after['max']})", data=after | {"before": before["y"]})

    def extract(self, selector: str, mode: str = "text", timeout_ms: int | None = None) -> ActionOutcome:
        try:
            raw = self._locator(selector).evaluate(EXTRACT_JS, mode)
        except PlaywrightError as exc:
            raise self._wrap(exc, f"extract {selector}", selector) from exc
        try:
            payload = normalize_payload(raw)
        except ValueError as exc:
            raise ControllerError(f"extract {selector}: {exc}") from exc
        size = len(payload)
        return ActionOutcome(detail=f"extracted {mode} from {selector} ({size} {'chars' if isinstance(payload, str) else 'items'})", data=payload)

    def screenshot(self, path: Path | str | None = None, full_page: bool = False, name: str | None = None) -> ActionOutcome:
        if path is None:
            if not self.artifacts_dir:
                raise ControllerError("no artifacts directory configured for screenshots")
            self._shots += 1
            safe = re.sub(r"[^A-Za-z0-9_-]+", "-", name or "shot").strip("-") or "shot"
            path = self.artifacts_dir / f"{self._shots:03d}-{safe}.png"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(path), full_page=full_page)
        except PlaywrightError as exc:
            raise self._wrap(exc, "screenshot") from exc
        return ActionOutcome(detail=f"saved {path.name}", screenshot=str(path))

    def wait(self, ms: int | None = None, selector: str | None = None, timeout_ms: int | None = None) -> ActionOutcome:
        timeout = timeout_ms or self.policy.action_timeout_ms
        try:
            if selector:
                self._locator(selector).wait_for(state="visible", timeout=timeout)
                return ActionOutcome(detail=f"{selector} became visible")
            self.page.wait_for_timeout(ms or 0)
        except PlaywrightError as exc:
            raise self._wrap(exc, f"wait {selector or ms}", selector) from exc
        return ActionOutcome(detail=f"waited {ms}ms")

    def back(self) -> ActionOutcome:
        try:
            self.page.go_back(wait_until="load", timeout=self.policy.action_timeout_ms)
        except PlaywrightError as exc:
            raise self._wrap(exc, "back") from exc
        return ActionOutcome(detail=f"went back to {self.url}")

    def forward(self) -> ActionOutcome:
        try:
            self.page.go_forward(wait_until="load", timeout=self.policy.action_timeout_ms)
        except PlaywrightError as exc:
            raise self._wrap(exc, "forward") from exc
        return ActionOutcome(detail=f"went forward to {self.url}")

    def reload(self) -> ActionOutcome:
        try:
            self.page.reload(wait_until="load", timeout=self.policy.action_timeout_ms)
        except PlaywrightError as exc:
            raise self._wrap(exc, "reload") from exc
        return ActionOutcome(detail=f"reloaded {self.url}")

    def visible_text(self) -> str:
        try:
            return self.page.evaluate("document.body ? document.body.innerText : ''")
        except PlaywrightError:
            return ""

    def is_visible(self, selector: str) -> bool:
        try:
            return self._locator(selector).is_visible()
        except PlaywrightError:
            return False

    def dismiss_candidates(self) -> list[str]:
        try:
            return list(self.page.evaluate(DISMISS_JS))
        except PlaywrightError:
            return []

    def observe(self, with_screenshot: bool = False, max_elements: int = 80) -> Observation:
        try:
            raw = self.page.evaluate(COLLECT_JS, max_elements)
        except PlaywrightError as exc:
            raise self._wrap(exc, "observe") from exc
        shot = self.screenshot(name="observe").screenshot if with_screenshot and self.artifacts_dir else None
        return observation_from_raw(self.url, self.title, raw, shot)

    # ---- dispatch ------------------------------------------------------
    def execute(self, action: Action, timeout_ms: int | None = None) -> ActionOutcome:
        """Run one validated action. Safety has already been assessed by the caller."""
        t = action.type
        if t == "navigate":
            return self.navigate(action.url or "", timeout_ms)
        if t == "click":
            return self.click(action.selector or "", timeout_ms)
        if t == "type":
            return self.type_text(action.selector or "", action.text or "", action.press_enter, timeout_ms)
        if t == "select":
            return self.select(action.selector or "", action.value or "", timeout_ms)
        if t == "scroll":
            return self.scroll(action.direction or "down", action.amount)
        if t == "extract":
            return self.extract(action.selector or "", action.mode, timeout_ms)
        if t == "screenshot":
            return self.screenshot(full_page=action.full_page, name=action.name)
        if t == "wait":
            return self.wait(action.ms, action.selector, timeout_ms)
        raise ControllerError(f"unsupported action type {t!r}")

