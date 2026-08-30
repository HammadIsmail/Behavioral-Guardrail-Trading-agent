from datetime import datetime

import httpx

from app.core.config import Settings
from app.schemas.account import AccountSnapshot, Position
from app.schemas.trade import ExecutedOrder, OrderSide, TradeProposal


class AlpacaClient:
    """The only file in the project that speaks HTTP to Alpaca."""

    def __init__(self, settings: Settings):
        self._base_url = settings.alpaca_base_url.rstrip("/")
        self._data_url = settings.alpaca_data_url.rstrip("/")
        self._headers = {
            "APCA-API-KEY-ID": settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
        }
        # One connection pool for the process instead of a fresh client per
        # call — the guardrail hits /account and /orders on every proposal.
        self._client = httpx.AsyncClient(headers=self._headers, timeout=10.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_account_snapshot(self) -> AccountSnapshot:
        account_resp = await self._client.get(f"{self._base_url}/v2/account")
        account_resp.raise_for_status()
        account = account_resp.json()

        positions_resp = await self._client.get(f"{self._base_url}/v2/positions")
        positions_resp.raise_for_status()
        raw_positions = positions_resp.json()

        positions = [
            Position(
                symbol=p["symbol"],
                qty=float(p["qty"]),
                market_value=float(p["market_value"]),
                unrealized_pl=float(p["unrealized_pl"]),
                current_price=float(p["current_price"]),
            )
            for p in raw_positions
        ]

        return AccountSnapshot(
            account_id=account["id"],
            buying_power=float(account["buying_power"]),
            cash=float(account["cash"]),
            portfolio_value=float(account["portfolio_value"]),
            equity=float(account["equity"]),
            positions=positions,
        )

    async def get_recent_orders(self, limit: int = 20) -> list[dict]:
        """Raw recent order history — used by guardrail rules to detect
        patterns like overtrading or revenge trading. Kept as raw dicts
        since rules only need a few fields (symbol, side, filled_at, qty)
        and we don't want to over-model data guardrail_rules.py owns the
        interpretation of."""
        resp = await self._client.get(
            f"{self._base_url}/v2/orders",
            params={"status": "all", "limit": limit, "direction": "desc"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        """Latest trade price per symbol from Alpaca's market data API.

        Returns only the symbols it could actually price — callers must
        handle a missing symbol rather than assume a price. Market data
        being unavailable is never allowed to raise: the guardrail has to
        keep working (with reduced precision) if this feed is down, so a
        failure comes back as an empty dict.
        """
        wanted = sorted({s.upper() for s in symbols if s})
        if not wanted:
            return {}

        try:
            resp = await self._client.get(
                f"{self._data_url}/v2/stocks/trades/latest",
                # feed=iex keeps this inside Alpaca's free data tier.
                params={"symbols": ",".join(wanted), "feed": "iex"},
            )
            resp.raise_for_status()
            trades = resp.json().get("trades", {})
        except (httpx.HTTPError, ValueError):
            return {}

        prices: dict[str, float] = {}
        for symbol, trade in trades.items():
            price = (trade or {}).get("p")
            if price is not None and float(price) > 0:
                prices[symbol.upper()] = float(price)
        return prices

    async def submit_order(self, proposal: TradeProposal) -> ExecutedOrder:
        payload = {
            "symbol": proposal.symbol,
            "qty": str(proposal.qty),
            "side": proposal.side.value,
            "type": "market",
            "time_in_force": "day",
        }
        resp = await self._client.post(f"{self._base_url}/v2/orders", json=payload)
        if resp.status_code >= 400:
            # Alpaca puts the real reason in the response body —
            # surface it instead of a bare "422 Unprocessable Entity"
            raise ValueError(f"Alpaca rejected order: {resp.status_code} {resp.text}")
        order = resp.json()

        return ExecutedOrder(
            order_id=order["id"],
            symbol=order["symbol"],
            qty=float(order["qty"]),
            side=OrderSide(order["side"]),
            status=order["status"],
            submitted_at=datetime.fromisoformat(
                order["submitted_at"].replace("Z", "+00:00")
            ),
        )
