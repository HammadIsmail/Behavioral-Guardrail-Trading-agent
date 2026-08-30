"""
Behavior gap — the number this project is built to surface.

DALBAR's finding is that the average equity investor underperforms the
index they're invested in, not because they picked wrong but because they
bought and sold at the wrong times. This module reproduces that comparison
against the user's own journal:

  passive  — every buy they made, still held today
  actual   — what their real buying and selling actually produced

The difference is their personal behavior gap.

Pure computation, no I/O: prices are handed in. That keeps the maths
unit-testable and keeps the Alpaca dependency in alpaca_client.py.
"""
from collections import defaultdict, deque

from app.schemas.trade import BehaviorGap, JournalEntry
from app.services.alpaca_client import AlpacaClient


def compute_behavior_gap(
    entries: list[JournalEntry], prices: dict[str, float]
) -> BehaviorGap:
    """Compare never-having-sold against what actually happened.

    Only executed entries with a recorded price count — a proposal that was
    flagged and cancelled never moved money, and an entry with no price
    can't be valued.

    Sells are matched against buys FIFO. A useful property of that choice:
    if the user never sells, actual and passive are identical and the gap is
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

        if entry.side.value == "buy":
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


class BehaviorGapService:
    """Wires the pure computation above to live prices."""

    def __init__(self, alpaca_client: AlpacaClient):
        self._alpaca = alpaca_client

    async def compute(self, entries: list[JournalEntry]) -> BehaviorGap:
        symbols = sorted(
            {e.symbol.upper() for e in entries if e.executed and e.price}
        )
        if not symbols:
            return compute_behavior_gap(entries, {})

        prices = await self._alpaca.get_latest_prices(symbols)
        return compute_behavior_gap(entries, prices)
