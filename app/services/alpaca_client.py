from datetime import date, datetime, timedelta

import httpx

from app.core.config import Settings
from app.schemas.account import AccountSnapshot, Position
from app.schemas.trade import ExecutedOrder, OrderSide, TradeProposal


class AlpacaClient:
    """The only file in the project that speaks HTTP to Alpaca."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str = "",
        secret_key: str = "",
        base_url: str = "",
        data_url: str = "",
    ):
        if settings is not None:
            api_key = settings.alpaca_api_key
            secret_key = settings.alpaca_secret_key
            base_url = settings.alpaca_base_url
            data_url = settings.alpaca_data_url
        self._base_url = base_url.rstrip("/")
        self._data_url = data_url.rstrip("/")
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }
        # One connection pool for the process instead of a fresh client per
        # call — the guardrail hits /account and /orders on every proposal,
        # and the agent loop runs every few minutes.
        self._client = httpx.AsyncClient(headers=self._headers, timeout=15.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- trading ----------

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

    async def get_clock(self) -> dict:
        """Market clock. The agent loop only trades when the market is open,
        so this gates every cycle.

        Never raises: a clock failure returns `is_open: False`, which makes the
        agent stand down rather than trade blind.
        """
        try:
            resp = await self._client.get(f"{self._base_url}/v2/clock")
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError):
            return {"is_open": False, "unavailable": True}

    async def get_portfolio_history(
        self, period: str = "1M", timeframe: str = "1D"
    ) -> dict:
        """Account equity over time — the raw material for the equity curve.

        Taken from Alpaca rather than reconstructed from the journal, so it
        reflects what the account actually did, including anything the journal
        never saw. Returns `{}` on failure: an equity chart is worth having and
        not worth failing a page load over.
        """
        try:
            resp = await self._client.get(
                f"{self._base_url}/v2/account/portfolio/history",
                params={"period": period, "timeframe": timeframe},
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError):
            return {}

    # ---------- market data ----------

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

    async def get_daily_closes(
        self, symbols: list[str], lookback_days: int = 60
    ) -> dict[str, list[float]]:
        """Daily closing prices per symbol, oldest first.

        The strategy needs moving averages, and closes are all it needs — so
        this returns bare float lists rather than modelling a full bar. Like
        `get_latest_prices`, a feed failure comes back empty instead of
        raising: no data means the strategy generates no signals, which is the
        safe outcome.
        """
        wanted = sorted({s.upper() for s in symbols if s})
        if not wanted:
            return {}

        start = (date.today() - timedelta(days=lookback_days)).isoformat()

        try:
            resp = await self._client.get(
                f"{self._data_url}/v2/stocks/bars",
                params={
                    "symbols": ",".join(wanted),
                    "timeframe": "1Day",
                    "start": start,
                    "feed": "iex",
                    "limit": 10000,
                    "adjustment": "split",
                },
            )
            resp.raise_for_status()
            bars = resp.json().get("bars", {})
        except (httpx.HTTPError, ValueError):
            return {}

        closes: dict[str, list[float]] = {}
        for symbol, symbol_bars in (bars or {}).items():
            series = [
                float(bar["c"])
                for bar in (symbol_bars or [])
                if bar.get("c") is not None and float(bar["c"]) > 0
            ]
            if series:
                closes[symbol.upper()] = series
        return closes

    async def get_movers(self, top: int = 10) -> dict:
        """Biggest gainers and losers across the US market.

        Alpaca's screener isn't guaranteed on the free data tier, so a failure
        returns `{}` and the caller falls back to deriving movers from our own
        universe's daily closes. An empty movers board is a worse outcome than a
        narrower one.
        """
        try:
            resp = await self._client.get(
                f"{self._data_url}/v1beta1/screener/stocks/movers",
                params={"top": top},
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError):
            return {}
