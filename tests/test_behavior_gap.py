from datetime import datetime, timedelta, timezone

from app.schemas.trade import JournalEntry, OrderSide
from app.services.behavior_gap import compute_behavior_gap

BASE = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)


def entry(side: str, qty: float, price: float, minute: int = 0, **kwargs) -> JournalEntry:
    return JournalEntry(
        timestamp=BASE + timedelta(minutes=minute),
        symbol=kwargs.pop("symbol", "NVDA"),
        qty=qty,
        side=OrderSide(side),
        executed=kwargs.pop("executed", True),
        price=price,
        **kwargs,
    )


def test_never_selling_means_no_gap():
    """The defining property: if you don't sell, your actual result and the
    hold-everything result are the same number."""
    gap = compute_behavior_gap([entry("buy", 10, 100.0)], {"NVDA": 120.0})

    assert gap.passive_pl == 200.0
    assert gap.actual_pl == 200.0
    assert gap.gap == 0.0


def test_selling_at_a_loss_before_a_recovery_shows_a_gap():
    entries = [
        entry("buy", 10, 100.0, minute=0),
        entry("sell", 10, 90.0, minute=10),
    ]
    gap = compute_behavior_gap(entries, {"NVDA": 120.0})

    assert gap.realized_pl == -100.0     # sold 10 @ 90 against a 100 basis
    assert gap.unrealized_pl == 0.0      # nothing left open
    assert gap.actual_pl == -100.0
    assert gap.passive_pl == 200.0       # had they held: 10 @ 100 -> 120
    assert gap.gap == 300.0              # the cost of that exit


def test_partial_sell_is_matched_fifo():
    entries = [
        entry("buy", 10, 100.0, minute=0),
        entry("buy", 10, 110.0, minute=5),
        entry("sell", 10, 90.0, minute=10),  # closes the $100 lot
    ]
    gap = compute_behavior_gap(entries, {"NVDA": 120.0})

    assert gap.realized_pl == -100.0            # 10 * (90 - 100)
    assert gap.unrealized_pl == 100.0           # 10 * (120 - 110)
    assert gap.passive_cost == 2_100.0
    assert gap.passive_value == 2_400.0


def test_proposals_that_never_executed_are_excluded():
    entries = [
        entry("buy", 10, 100.0, executed=False),
        entry("buy", 10, 100.0, executed=False, cancelled=True),
    ]
    gap = compute_behavior_gap(entries, {"NVDA": 120.0})

    assert gap.executed_trades == 0
    assert gap.passive_pl == 0.0
    assert gap.gap == 0.0


def test_unpriced_symbol_is_reported_not_guessed():
    entries = [entry("buy", 10, 100.0, symbol="ZZZZ")]
    gap = compute_behavior_gap(entries, {})

    assert gap.unpriced_symbols == ["ZZZZ"]
    assert gap.executed_trades == 0
    assert gap.passive_value == 0.0


def test_sell_with_no_recorded_buy_contributes_nothing():
    """A position opened before the journal existed has no cost basis here,
    so it must not invent a profit."""
    gap = compute_behavior_gap([entry("sell", 10, 90.0)], {"NVDA": 120.0})

    assert gap.realized_pl == 0.0
    assert gap.actual_pl == 0.0
