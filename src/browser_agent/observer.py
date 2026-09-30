"""Page observation: URL, title, visible text, interactive elements, tables."""

from __future__ import annotations

from typing import Any

from .models import ElementInfo, Observation

# Runs inside the page. Collects visible interactive elements with a selector
# that avoids ids that look auto-generated/volatile.
COLLECT_JS = r"""
(maxElements) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const stableId = (id) => id && /^[A-Za-z][\w-]*$/.test(id) && !/\d{3,}/.test(id) &&
    document.querySelectorAll('#' + CSS.escape(id)).length === 1;
  const q = (s) => s.replace(/"/g, '\\"');
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const cssPath = (el) => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 4 && cur !== document.body) {
      let part = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(' > ');
  };
  const selectorFor = (el) => {
    const tag = el.tagName.toLowerCase();
    const tid = el.getAttribute('data-testid');
    if (tid) return '[data-testid="' + q(tid) + '"]';
    if (stableId(el.id)) return '#' + CSS.escape(el.id);
    const name = el.getAttribute('name');
    if (name && ['input', 'select', 'textarea', 'button'].includes(tag)) return tag + '[name="' + q(name) + '"]';
    const text = clean(el.innerText);
    if ((tag === 'a' || tag === 'button') && text && text.length <= 40) return tag + ':has-text("' + q(text) + '")';
    return cssPath(el);
  };
  const out = [];
  const nodes = document.querySelectorAll('a[href], button, input, select, textarea, [role="button"], [role="link"], summary');
  for (const el of nodes) {
    if (out.length >= maxElements) break;
    if (el.type === 'hidden' || !visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    out.push({
      selector: selectorFor(el),
      tag,
      role: el.getAttribute('role') || '',
      text: clean(el.innerText || el.value || '').slice(0, 120),
      type: (el.getAttribute('type') || (tag === 'button' ? 'submit' : '')).toLowerCase(),
      name: el.getAttribute('name') || '',
      id: el.id || '',
      href: el.getAttribute('href') || '',
      aria_label: el.getAttribute('aria-label') || '',
      placeholder: el.getAttribute('placeholder') || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      form_action: el.form ? (el.form.getAttribute('action') || '') : '',
      in_form: !!el.form,
      visible: true,
      enabled: !el.disabled,
    });
  }
  const tables = Array.from(document.querySelectorAll('table')).filter(visible).slice(0, 10).map((t) => ({
    id: t.id || '',
    headers: Array.from(t.querySelectorAll('th')).map((h) => clean(h.innerText)).slice(0, 12),
    rows: t.querySelectorAll('tbody tr').length || Math.max(0, t.querySelectorAll('tr').length - 1),
  }));
  const headings = Array.from(document.querySelectorAll('h1, h2, h3')).filter(visible).map((h) => clean(h.innerText)).slice(0, 20);
  const overlayEl = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"], .modal, .overlay')).find(visible);
  return {
    text: (document.body ? document.body.innerText : '').slice(0, 6000),
    headings, tables, overlay: !!overlayEl, elements: out,
  };
}
"""

# Visible buttons inside dialogs / banners that look like "close" controls.
DISMISS_JS = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const re = /^(close|dismiss|no thanks|not now|maybe later|got it|ok|okay|accept|accept all|agree|continue|x|×|✕)$/i;
  const roots = document.querySelectorAll('[role="dialog"], [aria-modal="true"], .modal, .overlay, .popup, [class*="cookie"], [id*="cookie"], [class*="banner"]');
  const res = [];
  const seen = new Set();
  for (const root of roots) {
    if (!visible(root)) continue;
    for (const b of root.querySelectorAll('button, [role="button"], a')) {
      const label = (b.getAttribute('aria-label') || b.innerText || '').replace(/\s+/g, ' ').trim();
      if (!seen.has(b) && visible(b) && re.test(label)) {
        seen.add(b);
        b.setAttribute('data-ba-dismiss', String(res.length));
        res.push('[data-ba-dismiss="' + (res.length) + '"]');
      }
    }
  }
  return res;
}
"""


def observation_from_raw(url: str, title: str, raw: dict[str, Any], screenshot: str | None = None) -> Observation:
    return Observation(
        url=url,
        title=title,
        text=raw.get("text", ""),
        headings=raw.get("headings", []),
        elements=[ElementInfo(**e) for e in raw.get("elements", [])],
        tables=raw.get("tables", []),
        overlay=bool(raw.get("overlay")),
        screenshot=screenshot,
    )
