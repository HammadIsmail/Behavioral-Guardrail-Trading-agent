"""
Trade routes.

Thin orchestration only: the guardrail decision comes from
GuardrailService, the wording from ExplainerService, the record from
JournalService. No rule logic or prompt building lives here.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.dependencies import (
    get_alpaca_client,
    get_explainer_service,
    get_guardrail_service,
    get_journal_service,
)
from app.schemas.trade import (
    ExecutedOrder,
    GuardrailResult,
    JournalEntry,
    TradeProposal,
)
from app.services.alpaca_client import AlpacaClient
from app.services.explainer import ExplainerService
from app.services.guardrail_service import GuardrailService
from app.services.journal_service import JournalService

router = APIRouter(prefix="/trades", tags=["trades"])


class ProposalResponse(BaseModel):
    journal_entry_id: str
    proposal: TradeProposal
    result: GuardrailResult


class ExecutionResponse(BaseModel):
    executed: bool
    reason: str | None = None
    journal_entry_id: str | None = None
    guardrail_result: GuardrailResult | None = None
    order: ExecutedOrder | None = None


@router.post("/propose", response_model=ProposalResponse)
async def propose_trade(
    proposal: TradeProposal,
    guardrail: GuardrailService = Depends(get_guardrail_service),
    explainer: ExplainerService = Depends(get_explainer_service),
    journal: JournalService = Depends(get_journal_service),
):
    result = await guardrail.evaluate(proposal)
    result.explanation = await explainer.explain(result)

    entry = journal.add_entry(
        JournalEntry(
            timestamp=datetime.now(timezone.utc),
            symbol=proposal.symbol,
            qty=proposal.qty,
            side=proposal.side,
            guardrail_result=result,
            price=result.reference_price,
        )
    )

    return ProposalResponse(
        journal_entry_id=entry.id, proposal=proposal, result=result
    )


@router.post("/execute", response_model=ExecutionResponse)
async def execute_trade(
    proposal: TradeProposal,
    override: bool = False,
    journal_entry_id: str | None = None,
    guardrail: GuardrailService = Depends(get_guardrail_service),
    alpaca: AlpacaClient = Depends(get_alpaca_client),
    journal: JournalService = Depends(get_journal_service),
):
    """Execute a trade.

    The guardrail is re-run here rather than trusting a verdict from the
    proposal step: this endpoint is reachable directly, so the check has to
    happen on the path that actually places the order.
    """
    result = await guardrail.evaluate(proposal)

    if not result.approved and not override:
        return ExecutionResponse(
            executed=False,
            reason="flagged_awaiting_confirmation",
            journal_entry_id=journal_entry_id,
            guardrail_result=result,
        )

    order = await alpaca.submit_order(proposal)
    was_overridden = not result.approved and override

    # Update the proposal's existing row when we have it, so one trade is
    # one journal entry rather than a proposal row plus an execution row.
    entry = (
        journal.mark_executed(
            journal_entry_id,
            price=result.reference_price,
            was_overridden=was_overridden,
        )
        if journal_entry_id
        else None
    )

    if entry is None:
        entry = journal.add_entry(
            JournalEntry(
                timestamp=datetime.now(timezone.utc),
                symbol=proposal.symbol,
                qty=proposal.qty,
                side=proposal.side,
                guardrail_result=result,
                was_overridden=was_overridden,
                executed=True,
                price=result.reference_price,
            )
        )

    return ExecutionResponse(
        executed=True,
        journal_entry_id=entry.id,
        guardrail_result=result,
        order=order,
    )
