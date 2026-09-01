"""
The trading strategy: dual moving-average momentum with conviction sizing.

Deliberately simple and fully explainable — every signal carries the numbers
that produced it. A judge (or a user) should be able to read one signal and
know exactly why the agent wanted that trade.

Same discipline as the guardrail: `MomentumStrategy` is a pure function of the
data handed to it and does no I/O. `StrategyService` does the fetching. That's
what keeps the strategy unit-testable against fixed price series.
"""
from statistics import fmean

from app.schemas.account import AccountSnapshot
from app.schemas.strategy import StrategyDiagnostics, StrategySignal
from app.schemas.trade import OrderSide
from app.services.alpaca_client import AlpacaClient


class MomentumStrategy:
    """Buy when short-term momentum turns up, exit when it turns down.

    Entry: the short moving average crosses above the long one and we hold
    nothing in that name. Exit: the short average falls below the long one and
    we do hold it. Nothing else trades.

    Position size scales with how far the averages have separated — the
    strategy's own measure of conviction. This is the honest reason the
    guardrail matters: conviction scaling is a real and widespread technique,
    and it is exactly the mechanism by which a disciplined strategy talks
    itself into an oversized position.
    """

    # A 2% separation between the averages is treated as one full step of
    # extra conviction.
    CONVICTION_SPREAD_SCALE = 0.02
    # Don't bother with a position worth less than this.
    MIN_NOTIONAL = 50.0

    def __init__(
        self,
        short_window: int = 5,
        long_window: int = 20,
        base_position_pct: float = 0.08,
        max_conviction_multiple: float = 3.0,
        max_total_exposure_pct: float = 1.0,
    ):
        if short_window >= long_window:
            raise ValueError("short_window must be shorter than long_window")
        self.short_window = short_window
        self.long_window = long_window
        self.base_position_pct = base_position_pct
        self.max_conviction_multiple = max_conviction_multiple
        # Kept in step with OverexposureRule.MAX_TOTAL_EXPOSURE_PCT. The rule is
        # the authority — this is the strategy being a good citizen so it
        # doesn't re-propose a doomed buy on every cycle, which would bury the
        # journal in blocks and count one intent dozens of times in
        # `avoided_cost`.
        self.max_total_exposure_pct = max_total_exposure_pct

    def generate_signals(
        self,
        account: AccountSnapshot,
        closes: dict[str, list[float]],
        prices: dict[str, float],
    ) -> tuple[list[StrategySignal], list[StrategyDiagnostics]]:
        """Returns the trades it wants, plus a verdict for every symbol looked
        at — including the ones it decided not to trade, so the agent can show
        its reasoning rather than looking idle."""
        signals: list[StrategySignal] = []
        diagnostics: list[StrategyDiagnostics] = []

        held = {p.symbol.upper(): p for p in account.positions}

        # Capital already at work, plus whatever this cycle's earlier signals
        # have laid claim to. abs() so a short counts as exposure.
        exposure = sum(abs(p.market_value) for p in account.positions)
        exposure_limit = account.portfolio_value * self.max_total_exposure_pct
        committed = 0.0

        for symbol in sorted(closes):
            series = closes[symbol]
            position = held.get(symbol)
            held_qty = position.qty if position else 0.0

            if len(series) < self.long_window:
                diagnostics.append(
                    StrategyDiagnostics(
                        symbol=symbol, held=bool(held_qty), verdict="no data"
                    )
                )
                continue

            short_ma = fmean(series[-self.short_window :])
            long_ma = fmean(series[-self.long_window :])
            price = prices.get(symbol) or series[-1]

            if long_ma <= 0 or price <= 0:
                diagnostics.append(
                    StrategyDiagnostics(
                        symbol=symbol, held=bool(held_qty), verdict="no data"
                    )
                )
                continue

            spread_pct = (short_ma - long_ma) / long_ma
            diag = StrategyDiagnostics(
                symbol=symbol,
                short_ma=round(short_ma, 2),
                long_ma=round(long_ma, 2),
                price=round(price, 2),
                held=bool(held_qty),
                verdict="hold",
            )

            # --- exit ---
            if spread_pct < 0 and held_qty > 0:
                diag.verdict = "sell"
                diagnostics.append(diag)
                signals.append(
                    StrategySignal(
                        symbol=symbol,
                        side=OrderSide.sell,
                        qty=held_qty,
                        price=price,
                        short_ma=round(short_ma, 2),
                        long_ma=round(long_ma, 2),
                        spread_pct=round(spread_pct, 4),
                        conviction=1.0,
                        reason=(
                            f"{self.short_window}-day average "
                            f"({short_ma:,.2f}) has fallen "
                            f"{abs(spread_pct):.1%} below the "
                            f"{self.long_window}-day ({long_ma:,.2f}) — "
                            f"momentum has turned down, closing the position"
                        ),
                    )
                )
                continue

            # --- entry ---
            if spread_pct > 0 and held_qty == 0:
                conviction = min(
                    1.0 + spread_pct / self.CONVICTION_SPREAD_SCALE,
                    self.max_conviction_multiple,
                )
                target_notional = (
                    account.portfolio_value * self.base_position_pct * conviction
                )
                qty = float(int(target_notional / price))

                # Don't propose something Alpaca will bounce for lack of funds.
                if qty * price > account.buying_power:
                    qty = float(int(account.buying_power / price))

                # Don't propose something that would lever the book up. Buying
                # power is ~4x portfolio value on a paper account, so
                # affordability is a far weaker constraint than solvency.
                headroom = exposure_limit - exposure - committed
                if qty * price > headroom:
                    qty = float(int(headroom / price)) if headroom > 0 else 0.0

                if qty < 1 or qty * price < self.MIN_NOTIONAL:
                    diag.verdict = "hold"
                    diagnostics.append(diag)
                    continue

                committed += qty * price

                diag.verdict = "buy"
                diagnostics.append(diag)
                signals.append(
                    StrategySignal(
                        symbol=symbol,
                        side=OrderSide.buy,
                        qty=qty,
                        price=price,
                        short_ma=round(short_ma, 2),
                        long_ma=round(long_ma, 2),
                        spread_pct=round(spread_pct, 4),
                        conviction=round(conviction, 2),
                        reason=(
                            f"{self.short_window}-day average "
                            f"({short_ma:,.2f}) is {spread_pct:.1%} above the "
                            f"{self.long_window}-day ({long_ma:,.2f}) — "
                            f"momentum is up, sizing at {conviction:.1f}x base "
                            f"on that separation"
                        ),
                    )
                )
                continue

            diagnostics.append(diag)

        # Exits first, so a sell frees buying power for the buys in the same
        # cycle. Within the buys, strongest conviction first, so the agent's
        # per-cycle trade cap keeps the trades the strategy believes in most.
        signals.sort(key=lambda s: (s.side is OrderSide.buy, -s.conviction))
        return signals, diagnostics


class StrategyService:
    """Wires the pure strategy to live account state and market data."""

    def __init__(
        self,
        alpaca_client: AlpacaClient,
        strategy: MomentumStrategy,
        universe: list[str],
    ):
        self._alpaca = alpaca_client
        self._strategy = strategy
        self._universe = universe

    @property
    def universe(self) -> list[str]:
        return list(self._universe)

    @property
    def strategy(self) -> MomentumStrategy:
        """The configured strategy itself, so the settings page can show its
        thresholds without re-reading the environment."""
        return self._strategy

    async def generate(
        self, account: AccountSnapshot | None = None
    ) -> tuple[list[StrategySignal], list[StrategyDiagnostics]]:
        if account is None:
            account = await self._alpaca.get_account_snapshot()

        # Include anything currently held, even if it has dropped out of the
        # configured universe — otherwise the agent could never exit it.
        symbols = sorted(
            set(self._universe) | {p.symbol.upper() for p in account.positions}
        )

        closes = await self._alpaca.get_daily_closes(
            symbols, lookback_days=self._strategy.long_window * 4 + 15
        )
        prices = await self._alpaca.get_latest_prices(symbols)

        return self._strategy.generate_signals(account, closes, prices)
