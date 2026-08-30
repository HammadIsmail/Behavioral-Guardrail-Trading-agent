"""
Unit tests for the behavioral rules.

These are the easiest thing in the project to test because rules do no I/O
— everything they need arrives on RuleContext, so a test just builds fake
data and asserts on the flag.
"""
from datetime import datetime, timedelta, timezone

from app.schemas.account import AccountSnapshot, Position
from app.schemas.trade import OrderSide, TradeProposal
from app.services.guardrail_rules import (
    OversizedPositionRule,
    OvertradingRule,
    RevengeTradeRule,
    RuleContext,
)


def account(portfolio_value: float = 10_000.0, positions=None) -> AccountSnapshot:
    return AccountSnapshot(
        account_id="test",
        buying_power=portfolio_value,
        cash=portfolio_value,
        portfolio_value=portfolio_value,
        equity=portfolio_value,
        positions=positions or [],
    )


def proposal(side: str = "buy", qty: float = 10, symbol: str = "NVDA") -> TradeProposal:
    return TradeProposal(symbol=symbol, qty=qty, side=OrderSide(side))


def minutes_ago(n: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(minutes=n)
    return stamp.isoformat().replace("+00:00", "Z")


class TestOversizedPosition:
    rule = OversizedPositionRule()

    def test_small_buy_is_clean(self):
        ctx = RuleContext(
            proposal=proposal(qty=10),
            account=account(portfolio_value=10_000),
            reference_price=50.0,  # $500 of a $10k book
        )
        assert self.rule.check(ctx).triggered is False

    def test_large_buy_is_flagged(self):
        ctx = RuleContext(
            proposal=proposal(qty=100),
            account=account(portfolio_value=10_000),
            reference_price=50.0,  # $5,000 = 50% of the book
        )
        flag = self.rule.check(ctx)
        assert flag.triggered is True
        assert "50%" in flag.reason

    def test_selling_out_of_a_big_position_is_not_oversized(self):
        """Exiting reduces concentration — it must never be flagged as
        oversizing, which is what the rule used to do."""
        ctx = RuleContext(
            proposal=proposal(side="sell", qty=100),
            account=account(portfolio_value=10_000),
            reference_price=50.0,
        )
        assert self.rule.check(ctx).triggered is False

    def test_unknown_price_does_not_flag(self):
        """With no price there's no honest way to size the trade, so the
        rule stands down instead of guessing."""
        ctx = RuleContext(
            proposal=proposal(qty=10_000),
            account=account(portfolio_value=10_000),
            reference_price=None,
        )
        assert self.rule.check(ctx).triggered is False

    def test_uses_held_position_price(self):
        ctx = RuleContext(
            proposal=proposal(qty=100),
            account=account(
                portfolio_value=10_000,
                positions=[
                    Position(
                        symbol="NVDA",
                        qty=5,
                        market_value=1_000,
                        unrealized_pl=0,
                        current_price=200.0,
                    )
                ],
            ),
            reference_price=200.0,
        )
        assert self.rule.check(ctx).triggered is True

    def test_empty_account_is_clean(self):
        ctx = RuleContext(
            proposal=proposal(),
            account=account(portfolio_value=0),
            reference_price=50.0,
        )
        assert self.rule.check(ctx).triggered is False


class TestRevengeTrade:
    rule = RevengeTradeRule()

    def test_buy_shortly_after_a_sell_is_flagged(self):
        ctx = RuleContext(
            proposal=proposal(side="buy"),
            account=account(),
            recent_orders=[{"side": "sell", "filled_at": minutes_ago(5)}],
        )
        assert self.rule.check(ctx).triggered is True

    def test_old_sell_is_ignored(self):
        ctx = RuleContext(
            proposal=proposal(side="buy"),
            account=account(),
            recent_orders=[{"side": "sell", "filled_at": minutes_ago(120)}],
        )
        assert self.rule.check(ctx).triggered is False

    def test_sell_after_sell_is_not_revenge(self):
        ctx = RuleContext(
            proposal=proposal(side="sell"),
            account=account(),
            recent_orders=[{"side": "sell", "filled_at": minutes_ago(5)}],
        )
        assert self.rule.check(ctx).triggered is False

    def test_unfilled_order_is_ignored(self):
        ctx = RuleContext(
            proposal=proposal(side="buy"),
            account=account(),
            recent_orders=[{"side": "sell", "filled_at": None}],
        )
        assert self.rule.check(ctx).triggered is False

    def test_malformed_timestamp_does_not_raise(self):
        ctx = RuleContext(
            proposal=proposal(side="buy"),
            account=account(),
            recent_orders=[{"side": "sell", "filled_at": "not-a-date"}],
        )
        assert self.rule.check(ctx).triggered is False


class TestOvertrading:
    rule = OvertradingRule()

    def test_under_the_limit_is_clean(self):
        ctx = RuleContext(
            proposal=proposal(),
            account=account(),
            recent_orders=[
                {"side": "buy", "filled_at": minutes_ago(i)} for i in range(4)
            ],
        )
        assert self.rule.check(ctx).triggered is False

    def test_at_the_limit_is_flagged(self):
        ctx = RuleContext(
            proposal=proposal(),
            account=account(),
            recent_orders=[
                {"side": "buy", "filled_at": minutes_ago(i)} for i in range(5)
            ],
        )
        flag = self.rule.check(ctx)
        assert flag.triggered is True
        assert "5 trades" in flag.reason

    def test_trades_outside_the_hour_do_not_count(self):
        ctx = RuleContext(
            proposal=proposal(),
            account=account(),
            recent_orders=[
                {"side": "buy", "filled_at": minutes_ago(90 + i)} for i in range(10)
            ],
        )
        assert self.rule.check(ctx).triggered is False
