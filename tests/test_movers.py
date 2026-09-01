"""
Movers ranking.

`rank_movers` is the fallback path — used whenever Alpaca's screener isn't
available on the free tier — so it needs to be right on its own. Pure function of
a closes dict, so no network.
"""
from app.services.movers import rank_movers


def test_ranks_gainers_best_first():
    snapshot = rank_movers(
        {
            "AAA": [100.0, 110.0],  # +10%
            "BBB": [100.0, 102.0],  # +2%
            "CCC": [100.0, 105.0],  # +5%
        }
    )
    assert [m.symbol for m in snapshot.gainers] == ["AAA", "CCC", "BBB"]
    assert snapshot.losers == []


def test_ranks_losers_worst_first():
    """A movers board reads worst-first on the losing side."""
    snapshot = rank_movers(
        {
            "AAA": [100.0, 98.0],   # -2%
            "BBB": [100.0, 80.0],   # -20%
            "CCC": [100.0, 95.0],   # -5%
        }
    )
    assert [m.symbol for m in snapshot.losers] == ["BBB", "CCC", "AAA"]
    assert snapshot.gainers == []


def test_splits_gainers_and_losers():
    snapshot = rank_movers({"UP": [100.0, 110.0], "DOWN": [100.0, 90.0]})

    assert [m.symbol for m in snapshot.gainers] == ["UP"]
    assert [m.symbol for m in snapshot.losers] == ["DOWN"]


def test_computes_change_from_the_previous_close():
    snapshot = rank_movers({"AAA": [50.0, 100.0, 110.0]})
    mover = snapshot.gainers[0]

    assert mover.price == 110.0
    assert mover.change == 10.0
    assert mover.percent_change == 10.0
    assert mover.direction == "up"


def test_single_close_is_skipped():
    """One price is not a change."""
    snapshot = rank_movers({"AAA": [100.0]})
    assert snapshot.is_empty


def test_flat_symbols_appear_on_neither_side():
    snapshot = rank_movers({"FLAT": [100.0, 100.0]})
    assert snapshot.is_empty


def test_respects_the_top_limit():
    closes = {f"S{i}": [100.0, 100.0 + i] for i in range(1, 11)}
    snapshot = rank_movers(closes, top=3)
    assert len(snapshot.gainers) == 3


def test_reports_the_narrower_source_rather_than_hiding_it():
    snapshot = rank_movers({"AAA": [100.0, 110.0]})

    assert snapshot.source == "universe"
    assert snapshot.gainers[0].from_screener is False
    assert "screener was unavailable" in snapshot.note


def test_zero_previous_close_is_skipped_not_divided_by():
    snapshot = rank_movers({"AAA": [0.0, 100.0]})
    assert snapshot.is_empty


def test_empty_input_is_empty_board():
    assert rank_movers({}).is_empty
