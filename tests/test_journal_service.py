"""
Tests for the journal, including the summary crash: `guardrail_result` is
optional on an entry, and reading `.approved` off None used to take down
GET /journal/summary.
"""
from datetime import datetime, timezone

from app.schemas.trade import GuardrailResult, JournalEntry, OrderSide, RuleFlag
from app.services.journal_service import JournalService


def entry(**kwargs) -> JournalEntry:
    return JournalEntry(
        timestamp=datetime.now(timezone.utc),
        symbol=kwargs.pop("symbol", "NVDA"),
        qty=kwargs.pop("qty", 10),
        side=OrderSide(kwargs.pop("side", "buy")),
        **kwargs,
    )


def flagged_result() -> GuardrailResult:
    return GuardrailResult(
        approved=False,
        flags=[
            RuleFlag(rule_name="oversized_position", triggered=True, reason="too big")
        ],
    )


def test_summary_survives_an_entry_with_no_guardrail_result():
    journal = JournalService()
    journal.add_entry(entry(guardrail_result=None, executed=True))

    summary = journal.get_summary()

    assert summary["proposals"] == 1
    assert summary["executed_trades"] == 1
    # No recorded decision means it counts as neither flagged nor clean.
    assert summary["flagged_trades"] == 0
    assert summary["clean_trades"] == 0


def test_summary_counts_outcomes():
    journal = JournalService()
    clean = journal.add_entry(entry(guardrail_result=GuardrailResult(approved=True)))
    journal.mark_executed(clean.id, price=100.0)

    overridden = journal.add_entry(entry(guardrail_result=flagged_result()))
    journal.mark_executed(overridden.id, price=100.0, was_overridden=True)

    backed_off = journal.add_entry(entry(guardrail_result=flagged_result()))
    journal.mark_cancelled(backed_off.id)

    summary = journal.get_summary()

    assert summary["proposals"] == 3
    assert summary["executed_trades"] == 2
    assert summary["cancelled_trades"] == 1
    assert summary["flagged_trades"] == 2
    assert summary["overridden_trades"] == 1
    assert summary["clean_trades"] == 1


def test_executing_a_proposal_updates_it_instead_of_adding_a_row():
    """One proposed trade is one journal row through its whole life."""
    journal = JournalService()
    proposed = journal.add_entry(entry(guardrail_result=GuardrailResult(approved=True)))

    journal.mark_executed(proposed.id, price=123.45)

    assert len(journal.get_entries()) == 1
    stored = journal.get_entry(proposed.id)
    assert stored.executed is True
    assert stored.price == 123.45
    assert stored.status == "executed"


def test_status_labels():
    journal = JournalService()

    clean = journal.add_entry(entry(guardrail_result=GuardrailResult(approved=True)))
    assert clean.status == "clean"

    flagged = journal.add_entry(entry(guardrail_result=flagged_result()))
    assert flagged.status == "flagged"

    journal.mark_cancelled(flagged.id)
    assert journal.get_entry(flagged.id).status == "cancelled"

    journal.mark_executed(flagged.id, was_overridden=True)
    assert journal.get_entry(flagged.id).status == "overridden"


def test_marking_an_unknown_entry_is_a_no_op():
    journal = JournalService()
    assert journal.mark_executed("does-not-exist") is None
    assert journal.mark_cancelled("does-not-exist") is None
