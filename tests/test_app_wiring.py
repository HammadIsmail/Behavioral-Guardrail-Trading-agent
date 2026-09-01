"""
App wiring: does it import, and are the routes registered?

The rest of the suite tests services directly and never imports `app.main`, so
nothing else catches a broken router import, a duplicate path, or a route that
quietly disappeared in a refactor.

Deliberately **import-only** — no TestClient. Instantiating a client runs the
FastAPI lifespan, which starts the autonomous agent and would place real paper
orders from a test run.
"""
from app.main import app

EXPECTED_ROUTES = {
    # pages
    ("GET", "/"),
    ("GET", "/movers"),
    ("GET", "/analytics"),
    ("GET", "/decisions"),
    ("GET", "/chat"),
    ("GET", "/settings"),
    ("GET", "/health"),
    ("GET", "/account"),
    # trading
    ("POST", "/trades/propose"),
    ("POST", "/trades/execute"),
    # journal
    ("GET", "/journal/entries"),
    ("GET", "/journal/summary"),
    ("GET", "/journal/behavior-gap"),
    ("GET", "/journal/guardrail-impact"),
    # agent
    ("GET", "/agent/status"),
    ("POST", "/agent/run-once"),
    ("POST", "/agent/start"),
    ("POST", "/agent/stop"),
    ("POST", "/agent/auto-trade"),
    ("GET", "/agent/signals"),
    ("GET", "/agent/diagnostics"),
    # market
    ("GET", "/market/movers"),
    ("GET", "/market/equity"),
    # chat
    ("POST", "/chat"),
    # htmx fragments
    ("GET", "/fragments/agent"),
    ("POST", "/fragments/agent/run-once"),
    ("POST", "/fragments/agent/auto-trade"),
    ("GET", "/fragments/signals"),
    ("POST", "/fragments/chat"),
    ("POST", "/fragments/trades/propose"),
    ("POST", "/fragments/trades/execute"),
    ("POST", "/fragments/trades/cancel"),
    ("GET", "/fragments/journal"),
    ("GET", "/fragments/movers"),
}


def registered() -> set[tuple[str, str]]:
    found = set()
    for route in app.routes:
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            found.add((method, route.path))
    return found


def test_every_expected_route_is_registered():
    missing = EXPECTED_ROUTES - registered()
    assert not missing, f"missing routes: {sorted(missing)}"


def test_static_files_are_mounted():
    assert any(getattr(r, "path", "") == "/static" for r in app.routes)


def test_no_route_still_lives_under_ui():
    """The dashboard moved to `/` and nothing should remain at `/ui` (ADR-010)."""
    stragglers = [path for _, path in registered() if path.startswith("/ui")]
    assert stragglers == []


def test_no_duplicate_method_and_path():
    pairs = [
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
    ]
    duplicates = {pair for pair in pairs if pairs.count(pair) > 1}
    assert not duplicates, f"duplicate routes: {sorted(duplicates)}"


def test_lifespan_is_wired():
    """The agent is started from the lifespan handler — without it, `python
    run.py` serves a dashboard and never trades."""
    assert app.router.lifespan_context is not None
