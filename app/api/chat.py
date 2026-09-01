"""
Chat routes — natural-language questions about the user's own decisions.

Read-only. The chat service has no tools and no route to the order endpoint; it
is handed a summary of the journal and asked to explain it.
"""
from fastapi import APIRouter, Depends

from app.core.dependencies import (
    get_behavior_gap_service,
    get_chat_service,
    get_journal_service,
)
from app.schemas.chat import ChatQuestion, ChatReply
from app.services.behavior_gap import BehaviorGapService
from app.services.chat import ChatService, build_context
from app.services.journal_service import JournalService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatReply)
async def ask(
    payload: ChatQuestion,
    journal: JournalService = Depends(get_journal_service),
    behavior_gap: BehaviorGapService = Depends(get_behavior_gap_service),
    chat: ChatService = Depends(get_chat_service),
):
    """Ask about your own trading history.

    The answer is grounded in a factual summary of the journal, and the figures
    it was allowed to use come back in `context_used` so any claim can be
    checked against the numbers behind it.
    """
    entries = journal.get_entries()
    gap, impact = await behavior_gap.compute_all(entries)
    context = build_context(entries, journal.get_summary(), gap, impact)
    return await chat.answer(payload.question, context)
