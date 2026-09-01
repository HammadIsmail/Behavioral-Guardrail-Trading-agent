"""
Market movers — what's running and what's falling.

Alpaca's screener covers the whole US market but isn't guaranteed on the free
data tier. When it's unavailable this derives movers from the daily closes we
already fetch for the strategy, so the page degrades to a narrower sample rather
than to an empty screen. Which source was used is reported, not hidden.

Pure ranking logic is separated from the fetching for the same reason the rules
and the strategy are: it can then be tested against fixed numbers.
"""
from datetime import datetime, timezone

from app.schemas.market import Mover, MoversSnapshot
from app.services.alpaca_client import AlpacaClient


def rank_movers(closes: dict[str, list[float]], top: int = 5) -> MoversSnapshot:
    """Build a movers board from daily closes: last close against the one before.

    Symbols with fewer than two closes are skipped — one price is not a change.
    """
    moves: list[Mover] = []

    for symbol, series in closes.items():
        if len(series) < 2:
            continue
        previous, latest = series[-2], series[-1]
        if previous <= 0:
            continue
        change = latest - previous
        moves.append(
            Mover(
                symbol=symbol.upper(),
                price=round(latest, 2),
                change=round(change, 2),
                percent_change=round(change / previous * 100, 2),
                from_screener=False,
            )
        )

    moves.sort(key=lambda m: m.percent_change, reverse=True)
    gainers = [m for m in moves if m.percent_change > 0][:top]
    # Losers are ordered worst-first, which is how a movers board reads.
    losers = list(reversed([m for m in moves if m.percent_change < 0]))[:top]

    return MoversSnapshot(
        gainers=gainers,
        losers=losers,
        source="universe",
        as_of=datetime.now(timezone.utc),
        note="Derived from the agent's own universe — the market-wide screener was unavailable.",
    )


def _parse_screener(payload: dict, top: int) -> MoversSnapshot:
    def read(rows) -> list[Mover]:
        parsed: list[Mover] = []
        for row in (rows or [])[:top]:
            try:
                parsed.append(
                    Mover(
                        symbol=str(row["symbol"]).upper(),
                        price=float(row.get("price", 0.0)),
                        change=float(row.get("change", 0.0)),
                        percent_change=float(row.get("percent_change", 0.0)),
                        from_screener=True,
                    )
                )
            except (KeyError, TypeError, ValueError):
                # One malformed row shouldn't empty the board.
                continue
        return parsed

    return MoversSnapshot(
        gainers=read(payload.get("gainers")),
        losers=read(payload.get("losers")),
        source="screener",
        as_of=datetime.now(timezone.utc),
    )


class MoversService:
    def __init__(self, alpaca_client: AlpacaClient, universe: list[str]):
        self._alpaca = alpaca_client
        self._universe = universe

    async def get_movers(self, top: int = 5) -> MoversSnapshot:
        payload = await self._alpaca.get_movers(top=top)
        snapshot = _parse_screener(payload, top) if payload else None
        if snapshot is not None and not snapshot.is_empty:
            return snapshot

        closes = await self._alpaca.get_daily_closes(self._universe, lookback_days=10)
        return rank_movers(closes, top=top)
