from datetime import datetime

from pydantic import BaseModel


class Mover(BaseModel):
    """One symbol on the movers board."""
    symbol: str
    price: float
    change: float
    percent_change: float
    # True when this came from Alpaca's screener rather than being derived from
    # our own universe's daily bars. Surfaced so the page can be honest about
    # how wide the sample is.
    from_screener: bool = True

    @property
    def direction(self) -> str:
        return "up" if self.percent_change >= 0 else "down"


class MoversSnapshot(BaseModel):
    gainers: list[Mover] = []
    losers: list[Mover] = []
    # "screener" = the whole US market. "universe" = the ten symbols the agent
    # actually trades, computed from daily closes when the screener is
    # unavailable on the free tier.
    source: str = "screener"
    as_of: datetime
    note: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.gainers and not self.losers


class EquityPoint(BaseModel):
    at: datetime
    equity: float
    profit_loss: float
    profit_loss_pct: float


class PortfolioHistory(BaseModel):
    """Account equity over time — the equity curve.

    Sourced from Alpaca rather than reconstructed from the journal, so it
    reflects what the account actually did including anything the journal never
    saw.
    """
    points: list[EquityPoint] = []
    base_value: float = 0.0
    timeframe: str = "1D"

    @property
    def start_equity(self) -> float:
        return self.points[0].equity if self.points else self.base_value

    @property
    def end_equity(self) -> float:
        return self.points[-1].equity if self.points else self.base_value

    @property
    def total_pl(self) -> float:
        return round(self.end_equity - self.start_equity, 2)

    @property
    def total_pl_pct(self) -> float:
        start = self.start_equity
        if start <= 0:
            return 0.0
        return round((self.end_equity - start) / start * 100, 2)
