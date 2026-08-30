import pytest

from app.services.trade_parser import parse_trade_message


@pytest.mark.parametrize(
    "message,symbol,qty,side",
    [
        ("buy 50 shares of NVDA", "NVDA", 50, "buy"),
        ("sell 10 AAPL", "AAPL", 10, "sell"),
        ("BUY 5 TSLA", "TSLA", 5, "buy"),
        ("buy 2.5 shares of MSFT", "MSFT", 2.5, "buy"),
        ("sell all 30 of my AMD stock", "AMD", 30, "sell"),
        # Trailing filler words used to win over the real ticker here.
        ("I want to buy 50 NVDA now", "NVDA", 50, "buy"),
        ("please just sell 12 INTC today", "INTC", 12, "sell"),
        # An explicit $TICKER should win outright.
        ("buy 7 $GOOG", "GOOG", 7, "buy"),
        ("dump 100 F", "F", 100, "sell"),
    ],
)
def test_parses_common_phrasings(message, symbol, qty, side):
    proposal = parse_trade_message(message)
    assert proposal.symbol == symbol
    assert proposal.qty == qty
    assert proposal.side.value == side


@pytest.mark.parametrize(
    "message",
    [
        "",
        "   ",
        "50 shares of NVDA",       # no side
        "buy some NVDA",           # no quantity
        "buy 50 shares of",        # no symbol
    ],
)
def test_unparseable_messages_raise(message):
    with pytest.raises(ValueError):
        parse_trade_message(message)
