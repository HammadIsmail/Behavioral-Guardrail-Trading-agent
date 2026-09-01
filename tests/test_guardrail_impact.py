"""
Tests for the guardrail's counterfactual: what the blocked trades would have
done. This is the number that turns behavioral restraint into P&L, so its sign
convention matters and is asserted explicitly.
"""
from datetime import datetime, timedelta, timezone

from app.schemas.trade import (
    GuardrailResult,
    JournalEntry,
    OrderSide,
    RuleFlag,
    TradeSource,
)
from app.services.behavior_gap import compute_guardrail_impact

BASE = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)


def flagged(*rules: str) -> GuardrailResult:
    return GuardrailResult(
        approved=False,
        flags=[
            RuleFlag(rule_name=rule, triggered=True, reason=f"{rule} fired")
            for rule in rules
        ],
    )


def blocked_entry(
    side: str = "buy",
    qty: float = 10,
    price: float = 100.0,
    symbol: str = "NVDA",
    minute: int = 0,
    rules: tuple[str, ...] = ("oversized_position",),
) -> JournalEntry:
    return JournalEntry(
        timestamp=BASE + timedelta(minutes=minute),
        symbol=symbol,
        qty=qty,
        side=OrderSide(side),
        guardrail_result=flagged(*rules),
        blocked=True,
        price=price,
        source=TradeSource.agent,
        signal_reason="momentum turned up",
    )


def test_blocked_buy_that_would_have_lost_shows_savings():
    entries = [blocked_entry(qty=10, price=100.0)]
    impact = compute_guardrail_impact(entries, {"NVDA": 90.0})

    assert impact.blocked_trades == 1
    assert impact.blocked_buys == 1
    assert impact.avoided_cost == 1_000.0
    assert impact.avoided_pl == -100.0     # the trade would have lost $100
    assert impact.savings == 100.0         # so standing down saved $100


def test_blocked_buy_that_would_have_won_shows_a_cost():
    """Restraint isn't free. When the blocked trade would have paid off, that
    is reported rather than hidden."""
    entries = [blocked_entry(qty=10, price=100.0)]
    impact = compute_guardrail_impact(entries, {"NVDA": 120.0})

    assert impact.avoided_pl == 200.0
    assert impact.savings == -200.0


def test_blocked_sells_are_counted_but_carry_no_pl():
    """Declining to sell leaves the position on, and its outcome is already in
    the account's real P&L. Counting it here too would double count."""
    entries = [blocked_entry(side="sell", qty=10, price=100.0)]
    impact = compute_guardrail_impact(entries, {"NVDA": 130.0})

    assert impact.blocked_trades == 1
    assert impact.blocked_sells == 1
    assert impact.blocked_buys == 0
    assert impact.avoided_pl == 0.0
    assert impact.avoided_cost == 0.0


def test_executed_and_cancelled_entries_are_ignored():
    executed = JournalEntry(
        timestamp=BASE,
        symbol="NVDA",
        qty=10,
        side=OrderSide.buy,
        executed=True,
        price=100.0,
    )
    cancelled = JournalEntry(
        timestamp=BASE,
        symbol="NVDA",
        qty=10,
        side=OrderSide.buy,
        cancelled=True,
        price=100.0,
    )
    impact = compute_guardrail_impact([executed, cancelled], {"NVDA": 90.0})

    assert impact.blocked_trades == 0
    assert impact.savings == 0.0


def test_attributes_blocks_to_the_rules_that_fired():
    entries = [
        blocked_entry(rules=("oversized_position",)),
        blocked_entry(rules=("overtrading",), minute=5),
        blocked_entry(rules=("overtrading", "revenge_trade"), minute=10),
    ]
    impact = compute_guardrail_impact(entries, {"NVDA": 100.0})

    assert impact.by_rule == {
        "overtrading": 2,
        "oversized_position": 1,
        "revenge_trade": 1,
    }


def test_unpriced_symbol_is_reported_not_guessed():
    entries = [blocked_entry(symbol="ZZZZ")]
    impact = compute_guardrail_impact(entries, {})

    assert impact.blocked_trades == 1
    assert impact.unpriced_symbols == ["ZZZZ"]
    assert impact.savings == 0.0


def test_sums_across_several_blocks():
    entries = [
        blocked_entry(symbol="AAA", qty=10, price=100.0),           # -> 90: -100
        blocked_entry(symbol="BBB", qty=5, price=200.0, minute=5),  # -> 180: -100
    ]
    impact = compute_guardrail_impact(entries, {"AAA": 90.0, "BBB": 180.0})

    assert impact.avoided_cost == 2_000.0
    assert impact.avoided_pl == -200.0
    assert impact.savings == 200.0


def test_no_blocks_is_all_zeroes():
    impact = compute_guardrail_impact([], {})

    assert impact.blocked_trades == 0
    assert impact.savings == 0.0
    assert impact.by_rule == {}
