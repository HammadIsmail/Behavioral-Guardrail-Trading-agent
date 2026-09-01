"""
Tests for the momentum strategy.

`MomentumStrategy.generate_signals` is a pure function of an account snapshot
plus price series, so these build the series by hand and assert on the signal —
no network, no mocks.
"""
import pytest

from app.schemas.account import AccountSnapshot, Position
from app.services.strategy import MomentumStrategy

def account(
    portfolio_value: float = 100_000.0,
    buying_power: float = 100_000.0,
    positions=None,
) -> AccountSnapshot:
    return AccountSnapshot(
        account_id="test",
        buying_power=buying_power,
        cash=portfolio_value,
        portfolio_value=portfolio_value,
        equity=portfolio_value,
        positions=positions or [],
    )


def position(symbol: str, qty: float, price: float = 100.0) -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        market_value=qty * price,
        unrealized_pl=0.0,
        current_price=price,
    )


def rising(n: int = 30, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + step * i for i in range(n)]


def falling(n: int = 30, start: float = 130.0, step: float = 1.0) -> list[float]:
    return [start - step * i for i in range(n)]


def flat(n: int = 30, value: float = 100.0) -> list[float]:
    return [value] * n


class TestEntry:
    strategy = MomentumStrategy()

    def test_rising_series_produces_a_buy(self):
        signals, _ = strategy_run(self.strategy, {"NVDA": rising()}, account())
        assert len(signals) == 1
        assert signals[0].symbol == "NVDA"
        assert signals[0].side.value == "buy"
        assert signals[0].qty > 0

    def test_flat_series_produces_nothing(self):
        signals, diagnostics = strategy_run(self.strategy, {"NVDA": flat()}, account())
        assert signals == []
        assert diagnostics[0].verdict == "hold"

    def test_does_not_re_enter_a_held_position(self):
        signals, _ = strategy_run(
            self.strategy,
            {"NVDA": rising()},
            account(positions=[position("NVDA", 10)]),
        )
        assert signals == []

    def test_short_history_is_skipped_not_guessed(self):
        signals, diagnostics = strategy_run(
            self.strategy, {"NVDA": rising(n=5)}, account()
        )
        assert signals == []
        assert diagnostics[0].verdict == "no data"

    def test_reason_names_both_averages(self):
        signals, _ = strategy_run(self.strategy, {"NVDA": rising()}, account())
        reason = signals[0].reason
        assert "5-day" in reason and "20-day" in reason


class TestExit:
    strategy = MomentumStrategy()

    def test_falling_series_exits_a_held_position(self):
        signals, _ = strategy_run(
            self.strategy,
            {"NVDA": falling()},
            account(positions=[position("NVDA", 12)]),
        )
        assert len(signals) == 1
        assert signals[0].side.value == "sell"
        assert signals[0].qty == 12  # full exit

    def test_falling_series_with_no_position_does_nothing(self):
        """The strategy is long-only — it never opens a short."""
        signals, _ = strategy_run(self.strategy, {"NVDA": falling()}, account())
        assert signals == []


class TestConvictionSizing:
    def test_stronger_momentum_buys_more(self):
        strategy = MomentumStrategy(base_position_pct=0.08, max_conviction_multiple=3.0)

        gentle, _ = strategy_run(strategy, {"AAA": rising(step=0.2)}, account())
        steep, _ = strategy_run(strategy, {"AAA": rising(step=3.0)}, account())

        assert steep[0].conviction > gentle[0].conviction

    def test_conviction_is_capped(self):
        strategy = MomentumStrategy(max_conviction_multiple=2.0)
        signals, _ = strategy_run(strategy, {"AAA": rising(step=10.0)}, account())
        assert signals[0].conviction <= 2.0

    def test_conviction_scaling_can_exceed_the_guardrail_ceiling(self):
        """The point of the whole project: a disciplined strategy's own
        conviction scaling is what talks it into an oversized position. If this
        can never happen, the guardrail has nothing to catch."""
        strategy = MomentumStrategy(
            base_position_pct=0.08, max_conviction_multiple=3.0
        )
        book = account(portfolio_value=100_000.0)
        signals, _ = strategy_run(strategy, {"AAA": rising(step=5.0)}, book)

        pct_of_portfolio = signals[0].notional / book.portfolio_value
        assert pct_of_portfolio > 0.15

    def test_never_proposes_more_than_buying_power(self):
        strategy = MomentumStrategy()
        signals, _ = strategy_run(
            strategy,
            {"AAA": rising(step=5.0)},
            account(portfolio_value=100_000.0, buying_power=2_000.0),
        )
        for signal in signals:
            assert signal.notional <= 2_000.0

    def test_tiny_account_proposes_nothing(self):
        strategy = MomentumStrategy()
        signals, _ = strategy_run(
            strategy,
            {"AAA": rising()},
            account(portfolio_value=100.0, buying_power=100.0),
        )
        assert signals == []


class TestOrdering:
    def test_exits_come_before_entries(self):
        """Selling first frees buying power for the buys in the same cycle."""
        strategy = MomentumStrategy()
        signals, _ = strategy_run(
            strategy,
            {"UP": rising(), "DOWN": falling()},
            account(positions=[position("DOWN", 5)]),
        )
        assert [s.side.value for s in signals] == ["sell", "buy"]


class TestExposureCap:
    """The strategy respects the same 100%-of-portfolio ceiling
    OverexposureRule enforces, so it doesn't re-propose doomed buys every
    cycle once the book is full."""

    def test_stops_proposing_when_fully_invested(self):
        strategy = MomentumStrategy(max_total_exposure_pct=1.0)
        signals, _ = strategy_run(
            strategy,
            {"NEW": rising()},
            account(
                portfolio_value=100_000.0,
                buying_power=400_000.0,
                positions=[position("HELD", 1_000, 100.0)],  # $100k = 100%
            ),
        )
        assert signals == []

    def test_sizes_down_to_the_remaining_headroom(self):
        strategy = MomentumStrategy(max_total_exposure_pct=1.0)
        signals, _ = strategy_run(
            strategy,
            {"NEW": rising()},
            account(
                portfolio_value=100_000.0,
                buying_power=400_000.0,
                positions=[position("HELD", 950, 100.0)],  # $95k -> $5k headroom
            ),
        )
        assert len(signals) == 1
        assert signals[0].notional <= 5_000.0

    def test_exposure_binds_before_buying_power(self):
        """A paper account carries ~4x buying power, so affordability is a far
        weaker constraint than solvency. This is the gap that made the agent
        able to lever itself."""
        strategy = MomentumStrategy(max_total_exposure_pct=1.0)
        book = account(
            portfolio_value=100_000.0,
            buying_power=400_000.0,
            positions=[position("HELD", 900, 100.0)],  # $90k -> $10k headroom
        )
        signals, _ = strategy_run(strategy, {"NEW": rising(step=5.0)}, book)

        for signal in signals:
            assert signal.notional <= 10_000.0
            assert signal.notional < book.buying_power

    def test_total_across_one_cycle_stays_inside_the_cap(self):
        strategy = MomentumStrategy(
            base_position_pct=0.08,
            max_conviction_multiple=3.0,
            max_total_exposure_pct=0.30,
        )
        signals, _ = strategy_run(
            strategy,
            {"AAA": rising(step=5.0), "BBB": rising(step=5.0), "CCC": rising(step=5.0)},
            account(portfolio_value=100_000.0, buying_power=400_000.0),
        )
        total = sum(signal.notional for signal in signals)
        assert total <= 30_000.0
        assert len(signals) >= 2  # it still deploys, just within the limit

    def test_exits_are_never_capped(self):
        """Selling reduces exposure, so an over-invested book must still be
        able to get out."""
        strategy = MomentumStrategy(max_total_exposure_pct=1.0)
        signals, _ = strategy_run(
            strategy,
            {"DOWN": falling()},
            account(
                portfolio_value=100_000.0,
                positions=[position("DOWN", 1_500, 100.0)],  # $150k = 150%
            ),
        )
        assert len(signals) == 1
        assert signals[0].side.value == "sell"
        assert signals[0].qty == 1_500


def test_invalid_windows_rejected():
    with pytest.raises(ValueError):
        MomentumStrategy(short_window=20, long_window=5)


def strategy_run(strategy: MomentumStrategy, closes: dict, book: AccountSnapshot):
    """Prices default to the last close, which is what the real service does
    when market data is unavailable."""
    prices = {symbol: series[-1] for symbol, series in closes.items() if series}
    return strategy.generate_signals(book, closes, prices)
