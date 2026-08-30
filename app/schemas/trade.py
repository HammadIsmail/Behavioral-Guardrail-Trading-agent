
from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


class OrderSide(str, Enum):
    buy = "buy"
    sell = "sell"


class TradeProposal(BaseModel):
    """What the user (or agent parsing their message) wants to do."""
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
    created when the trade is proposed, then updated in place when the user
    executes or cancels it. One proposal is one row, so the journal reflects
    decisions rather than double-counting HTTP calls.
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
    # Price the trade was logged at. Required to compute the behavior gap
    # later — without it an executed trade can't be valued.
    price: float | None = None

    @property
    def was_flagged(self) -> bool:
        return self.guardrail_result is not None and not self.guardrail_result.approved

    @computed_field
    @property
    def status(self) -> str:
        """Single label for the UI and the API, so neither re-derives it."""
        if self.executed:
            return "overridden" if self.was_overridden else "executed"
        if self.cancelled:
            return "cancelled"
        return "flagged" if self.was_flagged else "clean"


class BehaviorGap(BaseModel):
    """
    The number the whole project exists to show: what the user's trades
    would have been worth if they had bought and never sold, versus what
    their actual in-and-out trading produced.

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
