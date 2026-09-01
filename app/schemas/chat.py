from datetime import datetime

from pydantic import BaseModel, Field


class ChatQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class ChatReply(BaseModel):
    """An answer grounded in the user's own trading journal.

    Read-only by construction: the chat has no tools and no path to the order
    endpoint. It is handed a summary of what already happened and asked to
    explain it. It cannot place, cancel or approve a trade — the same boundary
    that keeps the LLM out of the guardrail decision (ADR-001) applies here.
    """
    question: str
    answer: str
    asked_at: datetime
    # False when Groq was unavailable and the answer came from the deterministic
    # fallback, so the UI can say so rather than passing it off as generated.
    llm_used: bool = True
    # The figures the answer was allowed to draw on, so a reader can check it.
    context_used: dict = {}
