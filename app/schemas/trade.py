
from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


class OrderSide(str, Enum):
    buy = "buy"
    sell = "sell"


class TradeSource(str, Enum):
    """Who proposed the trade. The autonomous agent and a human at the
    dashboard go through the identical guardrail, but their outcomes are
    reported separately."""
    agent = "agent"
    user = "user"


class TradeProposal(BaseModel):
    """What the user (or the strategy) wants to do."""
    symbol: str
    qty: float = Field(gt=0)
    side: OrderSide


class RuleFlag(BaseModel):
    """One behavioral rule's verdict on a proposed trade."""
    rule_name: str
    triggered: bool
    reason: str


class GuardrailResult(BaseModel):
    """The full outcome of running a trade proposal through all rules."""
    approved: bool
    flags: list[RuleFlag] = []
    explanation: str = ""
    # Price the rules sized this trade against. None means neither the
    # account nor the market data feed could price the symbol, so any
    # size-based rule stood down.
    reference_price: float | None = None

    @computed_field
    @property
    def triggered_rules(self) -> list[str]:
        return [f.rule_name for f in self.flags if f.triggered]


class ExecutedOrder(BaseModel):
    order_id: str
    symbol: str
    qty: float
    side: OrderSide
    status: str
    submitted_at: datetime


class JournalEntry(BaseModel):
    """One trade decision, from proposal through to its outcome.

    A single entry covers the whole life of one proposed trade: it's
    created when the trade is proposed, then updated in place when it is
    executed, cancelled, or blocked. One proposal is one row, so the journal
    reflects decisions rather than double-counting HTTP calls.
    """
    id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime
    symbol: str
    qty: float
    side: OrderSide
    guardrail_result: GuardrailResult | None = None
    was_overridden: bool = False
    executed: bool = False
    cancelled: bool = False
    # The autonomous agent proposed this, the guardrail flagged it, and the
    # agent stood down. Distinct from `cancelled`, which is a human choosing
    # to back off. Blocked trades are what the counterfactual is computed on.
    blocked: bool = False
    # Price the trade was logged at. Required to compute the behavior gap and
    # the guardrail's counterfactual impact.
    price: float | None = None
    source: TradeSource = TradeSource.user
    # Why the strategy wanted this trade. Empty for human proposals.
    signal_reason: str = ""
    user_id: str = ""

    @property
    def was_flagged(self) -> bool:
        return self.guardrail_result is not None and not self.guardrail_result.approved

    @computed_field
    @property
    def status(self) -> str:
        """Single label for the UI and the API, so neither re-derives it."""
        if self.executed:
            return "overridden" if self.was_overridden else "executed"
        if self.blocked:
            return "blocked"
        if self.cancelled:
            return "cancelled"
        return "flagged" if self.was_flagged else "clean"


class BehaviorGap(BaseModel):
    """
    What the user's trades would have been worth if they had bought and
    never sold, versus what their actual in-and-out trading produced.

    `gap` is passive_pl - actual_pl, so a positive gap means the selling
    cost them money relative to sitting still.
    """
    passive_cost: float
    passive_value: float
    passive_pl: float

    realized_pl: float
    unrealized_pl: float
    actual_pl: float

    gap: float
    executed_trades: int
    unpriced_symbols: list[str] = []


class GuardrailImpact(BaseModel):
    """
    What the guardrail actually bought you, in dollars.

    For every trade the agent proposed and the guardrail stopped, this values
    that trade at the current price and asks what it would have produced.
    `savings` is the negation of that: positive means the blocked trades
    would have lost money, so standing down helped.
    """
    blocked_trades: int
    blocked_buys: int
    blocked_sells: int

    avoided_cost: float      # capital the blocked buys would have deployed
    avoided_pl: float        # P&L those blocked buys would have produced
    savings: float           # -avoided_pl; positive = the guardrail helped

    by_rule: dict[str, int] = {}
    unpriced_symbols: list[str] = []
