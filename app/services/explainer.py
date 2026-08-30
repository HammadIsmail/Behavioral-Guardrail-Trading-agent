
from groq import AsyncGroq

from app.core.config import Settings
from app.schemas.trade import GuardrailResult


class ExplainerService:
    """The only file that talks to Groq.

    It phrases a decision that GuardrailService has already made — it can
    never change `approved`. Because the decision is already final, an LLM
    failure must not fail the request: every path here falls back to a
    deterministic sentence built from the rule reasons themselves.
    """

    def __init__(self, settings: Settings):
        self._model = settings.groq_model
        # Only built when there's a key to build it with — an unconfigured
        # Groq shouldn't stop the app from starting, it should just mean
        # explanations come from the deterministic fallback.
        self._client = (
            AsyncGroq(api_key=settings.groq_api_key) if settings.groq_api_key else None
        )

    async def explain(self, result: GuardrailResult) -> str:
        triggered_reasons = [f.reason for f in result.flags if f.triggered]

        if self._client is None:
            return _fallback(result.approved, triggered_reasons)

        try:
            if result.approved:
                return await self._explain_clean()
            return await self._explain_flagged(triggered_reasons)
        except Exception:
            # Rate limit, timeout, bad key, malformed response — none of it
            # is allowed to lose a decision the rules already made.
            return _fallback(result.approved, triggered_reasons)

    async def _explain_clean(self) -> str:
        prompt = (
            "In one short, friendly sentence, tell a trader their trade "
            "looks fine and no behavioral red flags were detected. "
            "Do not add a disclaimer or mention risk."
        )
        return await self._complete(prompt)

    async def _explain_flagged(self, reasons: list[str]) -> str:
        joined = "; ".join(reasons)
        prompt = (
            "A trader is about to make a trade that triggered these "
            f"behavioral warning signs: {joined}. "
            "In 2-3 short sentences, explain calmly and non-judgmentally "
            "why this might be worth a second look, then ask if they'd "
            "like to proceed anyway or wait. Do not lecture. Do not use "
            "the word 'concerning'."
        )
        return await self._complete(prompt)

    async def _complete(self, prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=150,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("Groq returned an empty explanation")
        return content


def _fallback(approved: bool, reasons: list[str]) -> str:
    """Plain-language explanation with no LLM involved."""
    if approved:
        return "No behavioral red flags on this one — it looks consistent with your plan."

    if not reasons:
        return "This trade was flagged for review. Take a second look before proceeding."

    joined = reasons[0] if len(reasons) == 1 else "; ".join(reasons)
    return (
        f"Worth a second look: {joined}. "
        "Proceed anyway if this is still the trade you want, or wait it out."
    )
