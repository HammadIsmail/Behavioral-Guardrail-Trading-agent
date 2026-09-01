"""
Counterfactual valuation of the journal.

Two questions, both answered by pricing past decisions at today's price:

  Behavior gap      — what would holding every buy untouched have earned,
                      versus what the actual buying and selling earned?
  Guardrail impact  — the agent proposed these trades and the guardrail
                      stopped them. What would they have done?

The second is the one that turns a behavioral guardrail into a P&L argument:
it puts a dollar figure on restraint.

Pure computation, no I/O: prices are handed in. That keeps the maths
unit-testable and keeps the Alpaca dependency in alpaca_client.py.
"""
from collections import defaultdict, deque

from app.schemas.trade import BehaviorGap, GuardrailImpact, JournalEntry, OrderSide
from app.services.alpaca_client import AlpacaClient


def compute_behavior_gap(
    entries: list[JournalEntry], prices: dict[str, float]
) -> BehaviorGap:
    """Compare never-having-sold against what actually happened.

    Only executed entries with a recorded price count — a proposal that was
    flagged and blocked never moved money, and an entry with no price can't be
    valued.

    Sells are matched against buys FIFO. A useful property of that choice:
    if nothing is ever sold, actual and passive are identical and the gap is
    exactly zero, so any non-zero gap is attributable to selling decisions.
    """
    priced = {symbol.upper(): price for symbol, price in prices.items() if price > 0}

    executed = [
        e for e in entries if e.executed and e.price is not None and e.price > 0
    ]
    executed.sort(key=lambda e: e.timestamp)

    passive_cost = 0.0
    passive_value = 0.0
    realized_pl = 0.0
    unpriced: set[str] = set()

    # Open buy lots per symbol, oldest first, for FIFO sell matching.
    lots: dict[str, deque[list[float]]] = defaultdict(deque)
    counted = 0

    for entry in executed:
        symbol = entry.symbol.upper()
        current_price = priced.get(symbol)
        if current_price is None:
            unpriced.add(symbol)
            continue

        counted += 1
        qty = float(entry.qty)
        price = float(entry.price)

        if entry.side is OrderSide.buy:
            # Passive baseline: this purchase is never sold.
            passive_cost += qty * price
            passive_value += qty * current_price
            lots[symbol].append([qty, price])
            continue

        # Sell: realize against the oldest open lots.
        remaining = qty
        while remaining > 1e-9 and lots[symbol]:
            lot = lots[symbol][0]
            matched = min(remaining, lot[0])
            realized_pl += matched * (price - lot[1])
            lot[0] -= matched
            remaining -= matched
            if lot[0] <= 1e-9:
                lots[symbol].popleft()
        # Any leftover is a sell with no matching buy in the journal (a
        # position opened before the journal started). It has no cost basis
        # here, so it contributes nothing rather than a fabricated profit.

    unrealized_pl = 0.0
    for symbol, open_lots in lots.items():
        current_price = priced.get(symbol)
        if current_price is None:
            continue
        for qty, cost in open_lots:
            unrealized_pl += qty * (current_price - cost)

    passive_pl = passive_value - passive_cost
    actual_pl = realized_pl + unrealized_pl

    return BehaviorGap(
        passive_cost=round(passive_cost, 2),
        passive_value=round(passive_value, 2),
        passive_pl=round(passive_pl, 2),
        realized_pl=round(realized_pl, 2),
        unrealized_pl=round(unrealized_pl, 2),
        actual_pl=round(actual_pl, 2),
        gap=round(passive_pl - actual_pl, 2),
        executed_trades=counted,
        unpriced_symbols=sorted(unpriced),
    )


def compute_guardrail_impact(
    entries: list[JournalEntry], prices: dict[str, float]
) -> GuardrailImpact:
    """Value the trades the guardrail stopped.

    For each blocked **buy**, price it at today's market and ask what it would
    have produced. `savings` is the negation: positive means those trades would
    have lost money, so standing down was worth something.

    Blocked **sells** are counted but deliberately given no P&L figure.
    Declining to sell means the position stayed on, and its outcome is already
    inside the account's real P&L — attributing it here as well would double
    count it.
    """
    priced = {symbol.upper(): price for symbol, price in prices.items() if price > 0}

    blocked = [e for e in entries if e.blocked and e.price is not None and e.price > 0]

    blocked_buys = 0
    blocked_sells = 0
    avoided_cost = 0.0
    avoided_pl = 0.0
    by_rule: dict[str, int] = {}
    unpriced: set[str] = set()

    for entry in blocked:
        # Attribute the block to whichever rules fired, priced or not.
        if entry.guardrail_result is not None:
            for rule_name in entry.guardrail_result.triggered_rules:
                by_rule[rule_name] = by_rule.get(rule_name, 0) + 1

        if entry.side is OrderSide.buy:
            blocked_buys += 1
        else:
            blocked_sells += 1
            continue

        current_price = priced.get(entry.symbol.upper())
        if current_price is None:
            unpriced.add(entry.symbol.upper())
            continue

        avoided_cost += entry.qty * entry.price
        avoided_pl += entry.qty * (current_price - entry.price)

    return GuardrailImpact(
        blocked_trades=len(blocked),
        blocked_buys=blocked_buys,
        blocked_sells=blocked_sells,
        avoided_cost=round(avoided_cost, 2),
        avoided_pl=round(avoided_pl, 2),
        savings=round(-avoided_pl, 2),
        by_rule=dict(sorted(by_rule.items())),
        unpriced_symbols=sorted(unpriced),
    )


class BehaviorGapService:
    """Wires the pure computations above to live prices."""

    def __init__(self, alpaca_client: AlpacaClient):
        self._alpaca = alpaca_client

    async def compute(self, entries: list[JournalEntry]) -> BehaviorGap:
        prices = await self._prices_for(entries, executed=True, blocked=False)
        return compute_behavior_gap(entries, prices)

    async def compute_impact(self, entries: list[JournalEntry]) -> GuardrailImpact:
        prices = await self._prices_for(entries, executed=False, blocked=True)
        return compute_guardrail_impact(entries, prices)

    async def compute_all(
        self, entries: list[JournalEntry]
    ) -> tuple[BehaviorGap, GuardrailImpact]:
        """Both numbers off a single price fetch — the dashboard needs both on
        every render."""
        prices = await self._prices_for(entries, executed=True, blocked=True)
        return (
            compute_behavior_gap(entries, prices),
            compute_guardrail_impact(entries, prices),
        )

    async def _prices_for(
        self, entries: list[JournalEntry], *, executed: bool, blocked: bool
    ) -> dict[str, float]:
        symbols = {
            e.symbol.upper()
            for e in entries
            if e.price and ((executed and e.executed) or (blocked and e.blocked))
        }
        if not symbols:
            return {}
        return await self._alpaca.get_latest_prices(sorted(symbols))
