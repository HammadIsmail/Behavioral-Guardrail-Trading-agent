
from app.schemas.account import AccountSnapshot
from app.schemas.trade import GuardrailResult, RuleFlag, TradeProposal
from app.services.alpaca_client import AlpacaClient
from app.services.guardrail_rules import ALL_RULES, RuleContext


class GuardrailService:
    """Orchestrates the guardrail: gathers context, runs the rules, returns
    a decision.

    This is the only thing that decides `approved`. No LLM is involved
    here by design — the decision has to be deterministic, fast and
    testable. ExplainerService only phrases a result this service has
    already made.
    """

    def __init__(self, alpaca_client: AlpacaClient):
        self._alpaca = alpaca_client

    async def evaluate(self, proposal: TradeProposal) -> GuardrailResult:
        account = await self._alpaca.get_account_snapshot()
        recent_orders = await self._alpaca.get_recent_orders(limit=20)
        reference_price = await self._resolve_price(proposal.symbol, account)

        ctx = RuleContext(
            proposal=proposal,
            account=account,
            recent_orders=recent_orders,
            reference_price=reference_price,
        )

        flags: list[RuleFlag] = [rule.check(ctx) for rule in ALL_RULES]
        triggered = [f for f in flags if f.triggered]

        return GuardrailResult(
            approved=len(triggered) == 0,
            flags=flags,
            explanation="",  # filled in by explainer.py in the route layer
            reference_price=reference_price,
        )

    async def _resolve_price(
        self, symbol: str, account: AccountSnapshot
    ) -> float | None:
        """Price a symbol, preferring data we already have.

        A symbol the user already holds comes priced in the positions
        payload, so no extra market data call is needed. Only genuinely new
        symbols hit the data feed.
        """
        wanted = symbol.upper()
        for position in account.positions:
            if position.symbol.upper() == wanted and position.current_price > 0:
                return position.current_price

        prices = await self._alpaca.get_latest_prices([wanted])
        return prices.get(wanted)
