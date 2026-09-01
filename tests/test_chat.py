"""
Chat context and fallback.

The chat is read-only and must only speak from figures the journal produced, so
what goes into the context is the thing worth testing. The LLM call itself isn't
tested — the fallback is, because that's what ships when Groq is down.
"""
from datetime import datetime, timedelta, timezone

from app.schemas.trade import (
    BehaviorGap,
    GuardrailImpact,
    GuardrailResult,
    JournalEntry,
    OrderSide,
    RuleFlag,
    TradeSource,
)
from app.services.chat import _fallback, build_context

BASE = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)


def gap(**kwargs) -> BehaviorGap:
    defaults = dict(
        passive_cost=1000.0,
        passive_value=1200.0,
        passive_pl=200.0,
        realized_pl=-100.0,
        unrealized_pl=0.0,
        actual_pl=-100.0,
        gap=300.0,
        executed_trades=2,
    )
    return BehaviorGap(**{**defaults, **kwargs})


def impact(**kwargs) -> GuardrailImpact:
    defaults = dict(
        blocked_trades=2,
        blocked_buys=2,
        blocked_sells=0,
        avoided_cost=40000.0,
        avoided_pl=-400.0,
        savings=400.0,
        by_rule={"oversized_position": 2},
    )
    return GuardrailImpact(**{**defaults, **kwargs})


def entry(minute: int = 0, **kwargs) -> JournalEntry:
    return JournalEntry(
        timestamp=BASE + timedelta(minutes=minute),
        symbol=kwargs.pop("symbol", "NVDA"),
        qty=kwargs.pop("qty", 10),
        side=OrderSide(kwargs.pop("side", "buy")),
        price=kwargs.pop("price", 100.0),
        **kwargs,
    )


SUMMARY = {
    "proposals": 4,
    "executed_trades": 2,
    "blocked_trades": 2,
    "cancelled_trades": 0,
    "overridden_trades": 0,
}


class TestContext:
    def test_carries_both_headline_metrics(self):
        context = build_context([], SUMMARY, gap(), impact())

        assert context["behavior_gap"]["gap"] == 300.0
        assert context["guardrail_impact"]["savings"] == 400.0
        assert context["counts"]["proposals"] == 4

    def test_includes_why_a_trade_was_proposed_and_why_it_was_flagged(self):
        flagged = entry(
            guardrail_result=GuardrailResult(
                approved=False,
                flags=[
                    RuleFlag(
                        rule_name="oversized_position",
                        triggered=True,
                        reason="24% of your portfolio",
                    ),
                    RuleFlag(rule_name="overtrading", triggered=False, reason=""),
                ],
            ),
            blocked=True,
            source=TradeSource.agent,
            signal_reason="momentum turned up",
        )
        context = build_context([flagged], SUMMARY, gap(), impact())
        recent = context["recent_decisions"][0]

        assert recent["why_proposed"] == "momentum turned up"
        assert recent["why_flagged"] == ["24% of your portfolio"]
        assert recent["outcome"] == "blocked"
        assert recent["source"] == "agent"

    def test_untriggered_flags_are_left_out(self):
        clean = entry(guardrail_result=GuardrailResult(approved=True), executed=True)
        context = build_context([clean], SUMMARY, gap(), impact())

        assert context["recent_decisions"][0]["why_flagged"] == []

    def test_context_is_capped_to_recent_history(self):
        """A full journal dump would bury the signal and burn the window."""
        entries = [entry(minute=i) for i in range(40)]
        context = build_context(entries, SUMMARY, gap(), impact())

        assert len(context["recent_decisions"]) == 12

    def test_keeps_the_most_recent_not_the_oldest(self):
        entries = [entry(minute=i, symbol=f"S{i}") for i in range(20)]
        context = build_context(entries, SUMMARY, gap(), impact())

        assert context["recent_decisions"][-1]["symbol"] == "S19"


class TestFallback:
    def test_empty_journal_says_so_rather_than_inventing(self):
        answer = _fallback("how am I doing?", build_context([], {}, gap(), impact()))
        assert "nothing in your journal yet" in answer

    def test_reads_the_counts_back(self):
        answer = _fallback("summary?", build_context([], SUMMARY, gap(), impact()))

        assert "4 trades" in answer
        assert "2 executed" in answer
        assert "2 blocked" in answer

    def test_states_a_positive_behavior_gap_as_a_cost(self):
        answer = _fallback("my gap?", build_context([], SUMMARY, gap(gap=300.0), impact()))
        assert "behavior gap of $300.00" in answer

    def test_states_a_negative_gap_as_exits_beating_holding(self):
        answer = _fallback(
            "my gap?", build_context([], SUMMARY, gap(gap=-150.0), impact())
        )
        assert "exits beat holding by $150.00" in answer

    def test_zero_gap_is_explained_not_hidden(self):
        answer = _fallback("my gap?", build_context([], SUMMARY, gap(gap=0.0), impact()))
        assert "behavior gap is zero" in answer

    def test_reports_when_restraint_cost_money(self):
        answer = _fallback(
            "did the rules help?",
            build_context([], SUMMARY, gap(), impact(savings=-250.0)),
        )
        assert "cost you $250.00" in answer

    def test_admits_it_is_not_the_llm(self):
        answer = _fallback("hi", build_context([], SUMMARY, gap(), impact()))
        assert "Groq is unavailable" in answer
