from pydantic import BaseModel


class Position(BaseModel):
    symbol: str
    qty: float
    market_value: float
    unrealized_pl: float
    current_price: float


class AccountSnapshot(BaseModel):
    account_id: str
    buying_power: float
    cash: float
    portfolio_value: float
    equity: float
    positions: list[Position] = []