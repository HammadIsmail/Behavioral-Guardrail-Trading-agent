"""
Chat — natural-language questions about the user's own trading history.

The boundary that matters: **this is read-only by construction.** The chat has no
tools, no access to the order endpoint, and no path to `approved`. It is handed a
summary of what already happened and asked to explain it. That's the same rule
that keeps the LLM out of the guardrail decision (ADR-001), applied to a second
surface — an assistant that can be talked into placing a trade is a trading bot
with extra steps.

The context is assembled here rather than left to the model to fetch, so the
figures in an answer are always figures the journal actually produced. The prompt
forbids inventing numbers, and the numbers it was given are returned alongside the
answer so a reader can check it.
"""
from datetime import datetime, timezone

from groq import AsyncGroq

from app.core.config import Settings
from app.schemas.chat import ChatReply
from app.schemas.trade import BehaviorGap, GuardrailImpact, JournalEntry

_SYSTEM_PROMPT = """You are the reflective half of a behavioral trading guardrail.

The user runs an autonomous trading agent that proposes trades, and a set of
deterministic rules that block the ones showing behavioral warning signs
(oversized position, overexposure, revenge trading, overtrading).

Your job is to help them understand their own decisions. You are given a factual
summary of their journal. Rules you must follow:

- Use ONLY the figures provided. Never invent, estimate or extrapolate a number.
  If the summary doesn't contain what's needed, say plainly that you don't have it.
- You cannot place, cancel, modify or approve trades, and you must not imply you
  can. If asked to trade, say that trades go through the dashboard or the agent,
  and that every one is checked by the rules first.
- Do not give financial advice or predict prices. Talk about behavior and about
  what already happened.
- Be direct and plain. No hedging, no disclaimers, no lecturing. Two short
  paragraphs at most.
- A positive behavior gap means selling cost them money versus holding.
  Positive guardrail savings means the blocked trades would have lost money.
"""


def build_context(
    entries: list[JournalEntry],
    summary: dict,
    gap: BehaviorGap,
    impact: GuardrailImpact,
) -> dict:
    """The facts an answer is allowed to draw on.

    Deliberately small: counts, the two headline metrics, and the most recent
    decisions with their reasons. A full journal dump would bury the signal and
    burn the context window for no gain.
    """
    recent = []
    for entry in list(entries)[-12:]:
        recent.append(
            {
                "at": entry.timestamp.strftime("%Y-%m-%d %H:%M"),
                "side": entry.side.value,
                "qty": entry.qty,
                "symbol": entry.symbol,
                "price": entry.price,
                "outcome": entry.status,
                "source": entry.source.value,
                "why_proposed": entry.signal_reason or None,
                "why_flagged": (
                    [f.reason for f in entry.guardrail_result.flags if f.triggered]
                    if entry.guardrail_result
                    else []
                ),
            }
        )

    return {
        "counts": summary,
        "behavior_gap": {
            "if_held_everything": gap.passive_pl,
            "what_trading_did": gap.actual_pl,
            "gap": gap.gap,
            "realized": gap.realized_pl,
            "unrealized": gap.unrealized_pl,
            "trades_counted": gap.executed_trades,
        },
        "guardrail_impact": {
            "trades_stopped": impact.blocked_trades,
            "capital_not_deployed": impact.avoided_cost,
            "savings": impact.savings,
            "stopped_by_rule": impact.by_rule,
        },
        "recent_decisions": recent,
    }


class ChatService:
    def __init__(self, settings: Settings):
        self._model = settings.groq_model
        self._client = (
            AsyncGroq(api_key=settings.groq_api_key) if settings.groq_api_key else None
        )

    async def answer(self, question: str, context: dict) -> ChatReply:
        asked_at = datetime.now(timezone.utc)

        if self._client is None:
            return ChatReply(
                question=question,
                answer=_fallback(question, context),
                asked_at=asked_at,
                llm_used=False,
                context_used=context,
            )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Here is my journal summary:\n\n{context}\n\n"
                            f"My question: {question}"
                        ),
                    },
                ],
                temperature=0.3,
                max_tokens=400,
            )
            answer = (response.choices[0].message.content or "").strip()
            if not answer:
                raise ValueError("empty answer")
        except Exception:
            # Same stance as the explainer: a language failure degrades the
            # wording, never the facts.
            return ChatReply(
                question=question,
                answer=_fallback(question, context),
                asked_at=asked_at,
                llm_used=False,
                context_used=context,
            )

        return ChatReply(
            question=question,
            answer=answer,
            asked_at=asked_at,
            llm_used=True,
            context_used=context,
        )


def _fallback(question: str, context: dict) -> str:
    """A useful answer with no LLM involved: read the numbers back plainly."""
    counts = context.get("counts", {})
    gap = context.get("behavior_gap", {})
    impact = context.get("guardrail_impact", {})

    if not counts.get("proposals"):
        return (
            "There's nothing in your journal yet, so there's nothing for me to "
            "read back. Once the agent has proposed a few trades this will have "
            "something to say."
        )

    lines = [
        f"You've proposed {counts.get('proposals', 0)} trades: "
        f"{counts.get('executed_trades', 0)} executed, "
        f"{counts.get('blocked_trades', 0)} blocked by the rules, "
        f"{counts.get('cancelled_trades', 0)} cancelled and "
        f"{counts.get('overridden_trades', 0)} overridden."
    ]

    if gap.get("trades_counted"):
        gap_value = gap.get("gap", 0.0)
        if gap_value > 0:
            lines.append(
                f"Holding everything untouched would have earned "
                f"${gap.get('if_held_everything', 0):,.2f} against the "
                f"${gap.get('what_trading_did', 0):,.2f} your trading actually "
                f"produced — a behavior gap of ${gap_value:,.2f}."
            )
        elif gap_value < 0:
            lines.append(
                f"Your exits beat holding by ${abs(gap_value):,.2f}."
            )
        else:
            lines.append("Nothing you bought has been sold, so your behavior gap is zero.")

    if impact.get("trades_stopped"):
        savings = impact.get("savings", 0.0)
        verb = "saved you" if savings >= 0 else "cost you"
        lines.append(
            f"The rules stopped {impact['trades_stopped']} trades, which "
            f"{verb} ${abs(savings):,.2f}."
        )

    lines.append("(Groq is unavailable, so this is a plain read-out of your journal.)")
    return " ".join(lines)
