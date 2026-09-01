from datetime import datetime

from pydantic import BaseModel

from app.schemas.trade import OrderSide


class StrategySignal(BaseModel):
    """One trade the strategy wants to make, with its reasoning attached.

    The reasoning is carried on the signal rather than reconstructed later so
    the dashboard, the journal and the demo can all show *why* the agent
    wanted a trade — not just that it did.
    """
    symbol: str
    side: OrderSide
    qty: float
    price: float

    short_ma: float
    long_ma: float
    # How far the short average sits above/below the long one, as a fraction.
    # Drives position sizing.
    spread_pct: float
    conviction: float           # 1.0 = base size, higher = scaled up

    reason: str

    @property
    def notional(self) -> float:
        return self.qty * self.price


class StrategyDiagnostics(BaseModel):
    """Why the strategy did *not* trade a symbol — needed to demo that the
    agent is thinking rather than idle."""
    symbol: str
    short_ma: float | None = None
    long_ma: float | None = None
    price: float | None = None
    held: bool = False
    verdict: str = ""           # "buy", "sell", "hold", "no data"


class AgentCycleResult(BaseModel):
    """What one pass of the autonomous loop did."""
    ran_at: datetime
    market_open: bool
    signals_generated: int = 0
    executed: int = 0
    blocked: int = 0
    skipped_cap: int = 0        # dropped by the per-cycle trade cap
    errors: list[str] = []
    diagnostics: list[StrategyDiagnostics] = []


class AgentStatus(BaseModel):
    enabled: bool
    loop_running: bool
    market_open: bool | None = None
    interval_seconds: int
    universe: list[str]

    cycles_completed: int = 0
    total_proposed: int = 0
    total_executed: int = 0
    total_blocked: int = 0

    last_run_at: datetime | None = None
    last_error: str | None = None
    last_cycle: AgentCycleResult | None = None
