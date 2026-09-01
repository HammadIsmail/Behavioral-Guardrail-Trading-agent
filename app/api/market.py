"""
Market routes — what's moving, and the account's equity curve.
"""
from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_alpaca_client, get_movers_service
from app.schemas.market import EquityPoint, MoversSnapshot, PortfolioHistory
from app.services.alpaca_client import AlpacaClient
from app.services.movers import MoversService

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/movers", response_model=MoversSnapshot)
async def get_movers(
    top: int = Query(5, ge=1, le=25),
    movers: MoversService = Depends(get_movers_service),
):
    """Biggest gainers and losers.

    `source` says where the numbers came from: `screener` is the whole US market,
    `universe` means the screener was unavailable and these were derived from the
    ten symbols the agent trades. Narrower, but never empty.
    """
    return await movers.get_movers(top=top)


@router.get("/equity", response_model=PortfolioHistory)
async def get_equity_curve(
    period: str = Query("1M", pattern=r"^\d+[DWMA]$"),
    timeframe: str = Query("1D", pattern=r"^(1Min|5Min|15Min|1H|1D)$"),
    alpaca: AlpacaClient = Depends(get_alpaca_client),
):
    """Account equity over time.

    Read from Alpaca rather than reconstructed from the journal, so it reflects
    what the account actually did — including anything the journal never saw.
    """
    payload = await alpaca.get_portfolio_history(period=period, timeframe=timeframe)

    timestamps = payload.get("timestamp") or []
    equities = payload.get("equity") or []
    profits = payload.get("profit_loss") or []
    profit_pcts = payload.get("profit_loss_pct") or []

    points: list[EquityPoint] = []
    for index, stamp in enumerate(timestamps):
        equity = equities[index] if index < len(equities) else None
        if equity is None:
            # Alpaca pads the series with nulls outside market hours.
            continue
        points.append(
            EquityPoint(
                at=_from_epoch(stamp),
                equity=float(equity),
                profit_loss=float(profits[index]) if index < len(profits) and profits[index] is not None else 0.0,
                profit_loss_pct=(
                    float(profit_pcts[index]) * 100
                    if index < len(profit_pcts) and profit_pcts[index] is not None
                    else 0.0
                ),
            )
        )

    return PortfolioHistory(
        points=points,
        base_value=float(payload.get("base_value") or 0.0),
        timeframe=str(payload.get("timeframe") or timeframe),
    )


def _from_epoch(value):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(value), tz=timezone.utc)
