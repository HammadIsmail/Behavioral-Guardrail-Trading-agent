"""
Behavioral guardrail rules.

SOLID note (Open/Closed + Interface Segregation): every rule implements
the same tiny interface — `check(context) -> RuleFlag`. Adding a 4th rule
means writing a new class and adding it to ALL_RULES below; nothing here
or in guardrail_service.py needs to change. Rules take a RuleContext, not
raw Alpaca objects — they don't know or care that data came from Alpaca,
which is what makes them independently testable with fake data.

Rules never do I/O. Anything a rule needs from the outside world (an
account snapshot, order history, a market price) is resolved by
GuardrailService and handed in on the context.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.schemas.account import AccountSnapshot
from app.schemas.trade import RuleFlag, TradeProposal


@dataclass
class RuleContext:
    """Everything a rule might need to make a decision."""
    proposal: TradeProposal
    account: AccountSnapshot
    recent_orders: list[dict] = field(default_factory=list)
    # Best known price for the proposed symbol, or None when neither the
    # account's positions nor the market data feed could supply one.
    reference_price: float | None = None


class GuardrailRule(ABC):
    name: str

    @abstractmethod
    def check(self, ctx: RuleContext) -> RuleFlag:
        ...


def _clean(name: str) -> RuleFlag:
    return RuleFlag(rule_name=name, triggered=False, reason="")


class OversizedPositionRule(GuardrailRule):
    """Flags *entering* a position that's a large chunk of the account."""
    name = "oversized_position"
    MAX_POSITION_PCT = 0.15  # 15% of portfolio value in one trade

    def check(self, ctx: RuleContext) -> RuleFlag:
        # Selling is how you reduce concentration — exiting a large holding
        # is the responsible move, not an oversized bet. Only buys can
        # oversize a position.
        if ctx.proposal.side.value != "buy":
            return _clean(self.name)

        if ctx.account.portfolio_value <= 0:
            return _clean(self.name)

        # No price means no honest way to size this trade. Staying silent
        # beats flagging (or clearing) a trade based on a made-up number.
        if not ctx.reference_price or ctx.reference_price <= 0:
            return _clean(self.name)

        trade_value = ctx.proposal.qty * ctx.reference_price
        pct_of_portfolio = trade_value / ctx.account.portfolio_value

        if pct_of_portfolio > self.MAX_POSITION_PCT:
            return RuleFlag(
                rule_name=self.name,
                triggered=True,
                reason=(
                    f"this trade is about {pct_of_portfolio:.0%} of your "
                    f"portfolio — well above a typical {self.MAX_POSITION_PCT:.0%} "
                    f"single-position guideline"
                ),
            )
        return _clean(self.name)


class RevengeTradeRule(GuardrailRule):
    """Flags buying right after selling something.

    Known limitation: this is a timing heuristic, not true loss detection.
    It fires on any recent sell, profitable or not, because an Alpaca order
    object carries no realized P&L. Making it loss-aware needs cost-basis
    tracking — see CLAUDE.md.
    """
    name = "revenge_trade"
    LOOKBACK_MINUTES = 30

    def check(self, ctx: RuleContext) -> RuleFlag:
        if ctx.proposal.side.value != "buy":
            return _clean(self.name)

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.LOOKBACK_MINUTES)

        for order in ctx.recent_orders:
            filled_time = _parse_filled_at(order)
            if filled_time is None or filled_time < cutoff:
                continue

            if order.get("side") == "sell":
                return RuleFlag(
                    rule_name=self.name,
                    triggered=True,
                    reason=(
                        f"you sold within the last {self.LOOKBACK_MINUTES} "
                        f"minutes — this buy right after could be reacting to "
                        f"that, not a fresh decision"
                    ),
                )
        return _clean(self.name)


class OvertradingRule(GuardrailRule):
    """Flags too many trades in a short window."""
    name = "overtrading"
    MAX_TRADES_PER_HOUR = 5

    def check(self, ctx: RuleContext) -> RuleFlag:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_count = 0

        for order in ctx.recent_orders:
            filled_time = _parse_filled_at(order)
            if filled_time is not None and filled_time >= cutoff:
                recent_count += 1

        if recent_count >= self.MAX_TRADES_PER_HOUR:
            return RuleFlag(
                rule_name=self.name,
                triggered=True,
                reason=(
                    f"you've made {recent_count} trades in the last hour — "
                    f"that pace is often when analysis turns into impulse"
                ),
            )
        return _clean(self.name)


def _parse_filled_at(order: dict) -> datetime | None:
    """Alpaca timestamps are ISO-8601 with a Z suffix. A malformed or
    missing one means we can't reason about timing for that order, so it's
    skipped rather than allowed to blow up a whole evaluation."""
    filled_at = order.get("filled_at")
    if not filled_at:
        return None
    try:
        parsed = datetime.fromisoformat(str(filled_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


ALL_RULES: list[GuardrailRule] = [
    OversizedPositionRule(),
    RevengeTradeRule(),
    OvertradingRule(),
]
