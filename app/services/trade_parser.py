"""
Natural-language trade parsing.

Lives in services/ rather than in the route: turning "buy 50 shares of
NVDA" into a TradeProposal is business logic, and keeping it here means it
can be unit-tested without spinning up FastAPI.
"""
import re

from app.schemas.trade import OrderSide, TradeProposal

# Words that show up in trade phrasing but are never real stock symbols.
# Without this the symbol regex happily returns "NOW" for
# "I want to buy 50 NVDA now".
_STOPWORDS = {
    "BUY", "BUYING", "SELL", "SELLING", "SHARES", "SHARE", "STOCK", "STOCKS",
    "OF", "A", "AN", "THE", "I", "ME", "MY", "MINE", "WE", "US", "YOU",
    "WANT", "WANTED", "WANNA", "TO", "NOW", "TODAY", "PLEASE", "LET", "LETS",
    "DO", "IT", "IS", "AM", "ARE", "BE", "AT", "ON", "IN", "FOR", "AND",
    "OR", "GO", "GET", "PUT", "ADD", "ALL", "SOME", "MORE", "JUST", "CAN",
    "COULD", "WOULD", "SHOULD", "WILL", "MAYBE", "THINK", "FEEL", "LIKE",
    "MARKET", "ORDER", "TRADE", "POSITION", "WORTH", "EACH", "PER", "UP",
    "DOWN", "OUT", "OFF", "MY", "HIS", "HER", "THEIR", "THEM",
}

_SIDE_PATTERNS = (
    (OrderSide.buy, re.compile(r"\b(buy|buying|bought|long)\b")),
    (OrderSide.sell, re.compile(r"\b(sell|selling|sold|dump|exit|close)\b")),
)


def parse_trade_message(message: str) -> TradeProposal:
    """Parse a chat-style trade instruction.

    Handles the shapes a demo actually gets typed into it: "buy 50 shares
    of NVDA", "sell 10 AAPL", "I want to buy 5 $TSLA now". Deliberately
    small — this is not a real NLU pipeline. Raises ValueError with a
    user-facing message when it can't parse confidently.
    """
    text = message.lower().strip()
    if not text:
        raise ValueError("Tell me what you'd like to trade, e.g. 'buy 50 shares of NVDA'")

    side = None
    earliest = len(text) + 1
    for candidate_side, pattern in _SIDE_PATTERNS:
        match = pattern.search(text)
        # Whichever verb appears first wins, so "sell the AAPL I bought"
        # reads as a sell rather than latching onto "bought".
        if match and match.start() < earliest:
            side, earliest = candidate_side, match.start()
    if side is None:
        raise ValueError("I couldn't tell whether that's a buy or a sell")

    qty_match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not qty_match:
        raise ValueError("I couldn't find a quantity — how many shares?")
    qty = float(qty_match.group(1))
    if qty <= 0:
        raise ValueError("Quantity has to be greater than zero")

    symbol = _extract_symbol(message, qty_end=qty_match.end())
    if symbol is None:
        raise ValueError("I couldn't find a stock symbol in that")

    return TradeProposal(symbol=symbol, qty=qty, side=side)


def _extract_symbol(message: str, qty_end: int) -> str | None:
    """Find the ticker.

    An explicit $TICKER wins. Otherwise the first plausible token *after*
    the quantity is taken, which is where the ticker sits in almost every
    natural phrasing ("buy 50 shares of NVDA"). Only if nothing follows the
    quantity do we look before it.
    """
    upper = message.upper()

    dollar_tagged = re.search(r"\$([A-Z]{1,5})\b", upper)
    if dollar_tagged:
        return dollar_tagged.group(1)

    def candidates(segment: str) -> list[str]:
        return [
            token
            for token in re.findall(r"\b([A-Z]{1,5})\b", segment)
            if token not in _STOPWORDS
        ]

    after = candidates(upper[qty_end:])
    if after:
        return after[0]

    before = candidates(upper[:qty_end])
    if before:
        return before[-1]

    return None
