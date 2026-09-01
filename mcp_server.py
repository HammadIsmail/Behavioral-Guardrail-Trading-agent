"""
MCP server: the guardrail as a service any AI agent can call.

Run it with:

    python mcp_server.py

Or register it with an MCP client (Claude Desktop, Claude Code, etc.):

    {
      "mcpServers": {
        "alpaca-guardrail": {
          "command": "python",
          "args": ["C:/path/to/backend/mcp_server.py"]
        }
      }
    }

Why this exists rather than just consuming Alpaca's own MCP server: the
interesting thing this project has is not access to Alpaca — it's the
behavioral check in front of it. Exposing `evaluate_trade` over MCP means *any*
agent, not just ours, can route its trades through a behavioral guardrail
before they reach a broker. `execute_trade` here is guardrail-gated for the
same reason the HTTP API is: a check you can skip is not a check.

The tools operate in-process against the same services and the same SQLite
journal the web app uses, so trades placed here show up on the dashboard.
"""
import asyncio

from mcp.server.fastmcp import FastMCP

from app.core.dependencies import (
    get_agent_service,
    get_alpaca_client,
    get_behavior_gap_service,
    get_guardrail_service,
    get_journal_service,
    get_strategy_service,
)
from app.schemas.trade import JournalEntry, OrderSide, TradeProposal, TradeSource
from datetime import datetime, timezone

mcp = FastMCP("alpaca-guardrail")


@mcp.tool()
async def get_account() -> dict:
    """Live Alpaca paper account state: buying power, portfolio value, equity
    and every open position."""
    snapshot = await get_alpaca_client().get_account_snapshot()
    return snapshot.model_dump(mode="json")


@mcp.tool()
async def evaluate_trade(symbol: str, qty: float, side: str) -> dict:
    """Run a proposed trade through the behavioral guardrail WITHOUT placing it.

    Checks the trade against the account's live state and recent order history
    for three behavioral failure modes: oversized position, revenge trading,
    and overtrading.

    Returns `approved` plus a per-rule verdict and, for each triggered rule, a
    plain-language reason. The decision is deterministic — no language model is
    involved in producing it.

    Use this before executing any trade to find out whether it looks like a
    behavioral mistake rather than a decision.
    """
    proposal = TradeProposal(
        symbol=symbol.upper(), qty=qty, side=OrderSide(side.lower())
    )
    verdict = await get_guardrail_service().evaluate(proposal)

    journal = get_journal_service()
    entry = journal.add_entry(
        JournalEntry(
            timestamp=datetime.now(timezone.utc),
            symbol=proposal.symbol,
            qty=proposal.qty,
            side=proposal.side,
            guardrail_result=verdict,
            price=verdict.reference_price,
            source=TradeSource.agent,
            signal_reason="proposed via MCP",
        )
    )

    return {
        "journal_entry_id": entry.id,
        "approved": verdict.approved,
        "triggered_rules": verdict.triggered_rules,
        "flags": [f.model_dump() for f in verdict.flags],
        "reference_price": verdict.reference_price,
    }


@mcp.tool()
async def execute_trade(
    symbol: str, qty: float, side: str, override: bool = False
) -> dict:
    """Place a market order in Alpaca paper trading — guardrail-gated.

    The guardrail runs again here, on the same call that submits the order. If
    it flags the trade and `override` is false, nothing is placed and the
    reasons are returned instead. Pass `override=true` to proceed anyway; the
    override is recorded in the journal.
    """
    proposal = TradeProposal(
        symbol=symbol.upper(), qty=qty, side=OrderSide(side.lower())
    )
    verdict = await get_guardrail_service().evaluate(proposal)
    journal = get_journal_service()

    entry = journal.add_entry(
        JournalEntry(
            timestamp=datetime.now(timezone.utc),
            symbol=proposal.symbol,
            qty=proposal.qty,
            side=proposal.side,
            guardrail_result=verdict,
            price=verdict.reference_price,
            source=TradeSource.agent,
            signal_reason="submitted via MCP",
        )
    )

    if not verdict.approved and not override:
        journal.mark_blocked(entry.id)
        return {
            "executed": False,
            "reason": "flagged_awaiting_confirmation",
            "journal_entry_id": entry.id,
            "triggered_rules": verdict.triggered_rules,
            "flags": [f.model_dump() for f in verdict.flags],
        }

    try:
        order = await get_alpaca_client().submit_order(proposal)
    except ValueError as e:
        return {"executed": False, "reason": str(e), "journal_entry_id": entry.id}

    journal.mark_executed(
        entry.id,
        price=verdict.reference_price,
        was_overridden=not verdict.approved and override,
    )
    return {
        "executed": True,
        "journal_entry_id": entry.id,
        "was_overridden": not verdict.approved and override,
        "order": order.model_dump(mode="json"),
    }


@mcp.tool()
async def get_strategy_signals() -> list[dict]:
    """What the momentum strategy wants to trade right now, with the moving
    averages and conviction multiple behind each signal. Read-only."""
    signals, _ = await get_strategy_service().generate()
    return [s.model_dump(mode="json") for s in signals]


@mcp.tool()
async def run_agent_cycle() -> dict:
    """Run one full autonomous cycle: generate signals, guardrail each one,
    execute what passes and block what doesn't. Returns what the cycle did."""
    result = await get_agent_service().run_cycle()
    return result.model_dump(mode="json")


@mcp.tool()
async def get_agent_status() -> dict:
    """Whether the autonomous loop is running, and its cumulative counts of
    proposed, executed and blocked trades."""
    return get_agent_service().status.model_dump(mode="json")


@mcp.tool()
async def get_journal_summary() -> dict:
    """Counts of every trade decision recorded: proposed, executed, blocked,
    cancelled, overridden, and how many came from the autonomous agent."""
    return get_journal_service().get_summary()


@mcp.tool()
async def get_behavior_gap() -> dict:
    """The behavior gap: what holding every buy untouched would have earned
    versus what the actual buying and selling earned.

    `gap` positive means the selling cost money. Zero when nothing has been
    sold — that's a real property of the calculation, not an empty result.
    """
    journal = get_journal_service()
    gap = await get_behavior_gap_service().compute(journal.get_entries())
    return gap.model_dump(mode="json")


@mcp.tool()
async def get_guardrail_impact() -> dict:
    """What the guardrail bought you, in dollars.

    Prices every trade the guardrail stopped at today's market and asks what it
    would have produced. `savings` positive means those trades would have lost
    money, so standing down was worth something.
    """
    journal = get_journal_service()
    impact = await get_behavior_gap_service().compute_impact(journal.get_entries())
    return impact.model_dump(mode="json")


async def _shutdown() -> None:
    await get_alpaca_client().aclose()


if __name__ == "__main__":
    try:
        mcp.run()
    finally:
        asyncio.run(_shutdown())
