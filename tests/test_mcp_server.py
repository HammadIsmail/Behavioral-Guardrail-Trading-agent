"""
MCP server wiring.

The point of this file is the import itself: `mcp_server.py` is not imported by
the app or any other test, so nothing else would catch a wrong `FastMCP` import
path or a decorator that stopped registering. That was tracked as V-9.

Importing is side-effect free — `FastMCP(...)` and the `@mcp.tool()` decorators
only build and register. No service is constructed, no database file is created,
and no network call is made until a tool is actually invoked.
"""
import pytest

# Skip rather than fail if the optional dep isn't installed, so the suite stays
# green on a fresh checkout before `pip install -r requirements.txt`.
pytest.importorskip("mcp", reason="mcp not installed")

import mcp_server  # noqa: E402

EXPECTED_TOOLS = [
    "get_account",
    "evaluate_trade",
    "execute_trade",
    "get_strategy_signals",
    "run_agent_cycle",
    "get_agent_status",
    "get_journal_summary",
    "get_behavior_gap",
    "get_guardrail_impact",
]


def test_server_object_exists():
    assert mcp_server.mcp is not None


@pytest.mark.parametrize("name", EXPECTED_TOOLS)
def test_tool_is_defined(name):
    tool = getattr(mcp_server, name, None)
    assert tool is not None, f"{name} is missing from mcp_server"
    assert callable(tool)


def test_the_guardrail_is_exposed():
    """`evaluate_trade` is the reason this server exists — it's what lets any
    other agent route a trade through a behavioral check (ADR-019)."""
    assert callable(mcp_server.evaluate_trade)
    assert "guardrail" in (mcp_server.evaluate_trade.__doc__ or "").lower()


def test_execute_tool_documents_that_it_is_gated():
    """An MCP client must not be able to skip the guardrail (ADR-002), and the
    tool description is what tells a calling model that."""
    doc = (mcp_server.execute_trade.__doc__ or "").lower()
    assert "guardrail" in doc
    assert "override" in doc
