# Development checklist

Status of each requirement and **how it was actually verified**. Environment: Linux, Python 3.11.15, Node 22, Playwright 1.56.0 with Chromium 141, headless. Nothing here was run on other platforms or browsers.

Legend: ✅ implemented and verified by executing it · ⚠️ implemented, verified only partially · ❌ not done

## Components

| # | Requirement | Status | Verification |
| --- | --- | --- | --- |
| 1 | Browser controller (navigate, click, type, scroll, extract text, screenshot, wait, back/forward) | ✅ | `tests/test_controller.py` – 20 tests in real Chromium, incl. PNG signature check, hidden-row extraction, blocked cross-domain `fetch`, dialog dismissal, pop-up closing |
| 2 | Action schema (navigate, click, type, select, scroll, extract, screenshot, wait) | ✅ | `tests/test_actions.py` – valid + 15 invalid shapes rejected |
| 3 | Planner (goal → structured plan) | ✅ rules planner · ⚠️ local model planner | Rules planner: `tests/test_planner.py`, deterministic. Ollama planner: tested against a **stub** HTTP server (valid plan, invalid plan → fallback, unreachable → fallback, non-loopback refused). **Never run against a real local model.** |
| 4 | Observer (URL, title, text, DOM info, screenshot) | ✅ | `test_observe_reports_elements_and_tables`; used by every e2e run |
| 5 | Verification after actions | ✅ | `test_verification_failure_is_reported_not_hidden` (click lands on wrong page → run fails, `verified:false` logged); every e2e step records `verified` |
| 6 | Recovery (stale selector, navigation change, missing element, timeout, unexpected state) | ✅ | e2e: stale `#nav-pricing` id, link renamed to "Plans", blocking modal, wrong-page click; unit (scripted controller): retry counts, timeout growth + cap, give-up after `max_retries`, policy errors never retried |
| 7 | Safety (allowed/blocked domains, max actions, timeout, confirmation, no purchases, no account deletion, no credential harvesting) | ✅ | `tests/test_safety.py` (29 tests) + e2e: purchase, account deletion and password typing blocked **even with auto-approval**; submit needs confirmation; disallowed/blocked domains refused; action limit aborts; task timeout aborts. Mutation check: disabling the financial rule made 8 tests fail. |
| 8 | Structured extraction (JSON/CSV/Markdown) | ✅ | `tests/test_extraction.py`; CLI writes CSV/Markdown in `tests/test_cli.py`; e2e reads real table from the demo site |
| 9 | Execution logs | ✅ | `tests/test_logs.py`; `test_run_artifacts_are_written` checks the JSONL fields (timestamp, action, target, result, error, retry_count) |
| 10 | UI dashboard | ✅ | `tests/test_ui.py` drives the built dashboard with Playwright: runs a task, sees plan steps, history rows, pricing table, screenshot (natural width > 100), CSV tab; confirmation modal deny; planning error shown. Screenshot in `docs/dashboard.png` was produced by that test. |
| 11 | Local test website | ✅ | `src/browser_agent/demo_site/static`: navigation, forms, tables, async content, buttons, changing ids/classes, renamed link, blocking modal, delete-account and pay buttons |
| 12 | Tests | ✅ | see below |
| 13 | CLI (`run`, `test`, plus `plan`, `demo`, `serve`) | ✅ | `tests/test_cli.py` runs the CLI as a subprocess (JSON/CSV/Markdown, exit codes 0/1/3, `--yes`, `plan`, `test`); manually ran `browser-agent run "Extract the pricing table from the local demo website" --format markdown` |
| 14 | README with all required sections | ✅ | Overview, Architecture, Safety model, Installation, Playwright setup, local demo, CLI, Python API, Action schema, Logs, Testing, Limitations, Roadmap, License |

## Quality gates (last full run)

| Check | Command | Result |
| --- | --- | --- |
| Backend + browser + e2e tests | `pytest` | **141 passed**, 0 failed, 0 skipped (1 third-party deprecation warning from the test client) |
| Built-in end-to-end self test | `browser-agent test` | **14/14 scenarios passed** in real Chromium |
| Frontend type-check + build | `cd frontend && npm run build` | `tsc --noEmit` clean, Vite build succeeded |
| CLI, real run | `browser-agent run "Extract the pricing table from the local demo website"` | exit 0, 4 plans extracted |
| CLI, goal with no site | `browser-agent run "Extract the pricing table"` | exit 1, planning error, no browser opened, nothing fabricated |
| Backend server | `browser-agent serve` + `curl` | health OK, UI served, task created over HTTP completed and returned the Starter plan |
| Wheel packaging | `pip wheel . --no-deps` | wheel contains the demo site and built UI |

## Bugs found and fixed while verifying

* The controller closed its own first page (the "new page" hook was registered before the page was created).
* Dismiss-overlay selectors collided when a button matched two container selectors, so recovery clicked a missing element.
* "Load more" was followed immediately by extraction; the planner now inserts an explicit wait for asynchronously appended rows.
* Missing-element recovery stopped after one wait when no alternative existed; it now keeps retrying up to `max_retries`.
* Dashboard: the idle state disabled the Run button.
* Test helper argument name collision (`result`).

## Known gaps / not verified

* Local model planner against a real model (only a stub server).
* Other OSes, Python 3.10/3.12, Firefox/WebKit, headed mode.
* The GitHub Actions workflow (`.github/workflows/ci.yml`) has not been executed.
* Third-party websites: intentionally not tested; behaviour on real sites (iframes, shadow DOM, bot protection) is unknown.
* Element-level safety checks depend on page text/attributes and are not a security boundary (see README → Limitations).
