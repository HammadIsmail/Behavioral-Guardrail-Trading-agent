"""
Journal routes — read-only views into trade history for the dashboard.
"""
from fastapi import APIRouter, Depends

from app.core.dependencies import get_behavior_gap_for_user, get_journal_for_user, get_current_user
from app.schemas.trade import BehaviorGap, GuardrailImpact
from app.services.behavior_gap import BehaviorGapService
from app.services.journal_service import JournalService

router = APIRouter(prefix="/journal", tags=["journal"])


@router.get("/entries")
async def get_entries(journal: JournalService = Depends(get_journal_for_user)):
    return journal.get_entries()


@router.get("/summary")
async def get_summary(journal: JournalService = Depends(get_journal_for_user)):
    return journal.get_summary()


@router.get("/behavior-gap", response_model=BehaviorGap)
async def get_behavior_gap(
    journal: JournalService = Depends(get_journal_for_user),
    behavior_gap: BehaviorGapService = Depends(get_behavior_gap_for_user),
):
    """What holding everything untouched would have earned, versus what the
    actual buying and selling earned."""
    return await behavior_gap.compute(journal.get_entries())


@router.get("/guardrail-impact", response_model=GuardrailImpact)
async def get_guardrail_impact(
    journal: JournalService = Depends(get_journal_for_user),
    behavior_gap: BehaviorGapService = Depends(get_behavior_gap_for_user),
):
    """The trades the guardrail stopped, priced at today's market.

    `savings` positive means those trades would have lost money — the dollar
    value of the agent's restraint.
    """
    return await behavior_gap.compute_impact(journal.get_entries())
