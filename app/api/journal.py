"""
Journal routes — read-only views into trade history for the dashboard.
"""
from fastapi import APIRouter, Depends

from app.core.dependencies import get_behavior_gap_service, get_journal_service
from app.schemas.trade import BehaviorGap
from app.services.behavior_gap import BehaviorGapService
from app.services.journal_service import JournalService

router = APIRouter(prefix="/journal", tags=["journal"])


@router.get("/entries")
async def get_entries(journal: JournalService = Depends(get_journal_service)):
    return journal.get_entries()


@router.get("/summary")
async def get_summary(journal: JournalService = Depends(get_journal_service)):
    return journal.get_summary()


@router.get("/behavior-gap", response_model=BehaviorGap)
async def get_behavior_gap(
    journal: JournalService = Depends(get_journal_service),
    behavior_gap: BehaviorGapService = Depends(get_behavior_gap_service),
):
    """What holding everything untouched would have earned, versus what the
    user's actual buying and selling earned."""
    return await behavior_gap.compute(journal.get_entries())
