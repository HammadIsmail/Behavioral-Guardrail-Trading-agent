"""
Tests for the journal.

Covers the SQLite round trip, the lifecycle transitions, and the summary crash
that used to take down GET /journal/summary: `guardrail_result` is nullable, and
reading `.approved` off None raised.
"""
from datetime import datetime, timezone

from app.schemas.trade import (
    GuardrailResult,
    JournalEntry,
    OrderSide,
    RuleFlag,
    TradeSource,
)
from app.services.journal_service import JournalService


def entry(**kwargs) -> JournalEntry:
    return JournalEntry(
        timestamp=kwargs.pop("timestamp", datetime.now(timezone.utc)),
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
        reference_price=172.4,
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

    stood_down = journal.add_entry(
        entry(guardrail_result=flagged_result(), source=TradeSource.agent)
    )
    journal.mark_blocked(stood_down.id)

    summary = journal.get_summary()

    assert summary["proposals"] == 4
    assert summary["executed_trades"] == 2
    assert summary["cancelled_trades"] == 1
    assert summary["blocked_trades"] == 1
    assert summary["flagged_trades"] == 3
    assert summary["overridden_trades"] == 1
    assert summary["clean_trades"] == 1
    assert summary["agent_proposals"] == 1
    assert summary["agent_blocked"] == 1


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
    assert journal.get_entry(clean.id).status == "clean"

    flagged = journal.add_entry(entry(guardrail_result=flagged_result()))
    assert journal.get_entry(flagged.id).status == "flagged"

    journal.mark_cancelled(flagged.id)
    assert journal.get_entry(flagged.id).status == "cancelled"

    journal.mark_blocked(flagged.id)
    assert journal.get_entry(flagged.id).status == "blocked"

    journal.mark_executed(flagged.id, was_overridden=True)
    assert journal.get_entry(flagged.id).status == "overridden"


def test_marking_blocked_clears_other_outcomes():
    """The outcome flags are mutually exclusive — a blocked trade must not also
    read as executed, or it would be counted in the behavior gap."""
    journal = JournalService()
    e = journal.add_entry(entry(guardrail_result=flagged_result(), executed=True))

    journal.mark_blocked(e.id)
    stored = journal.get_entry(e.id)

    assert stored.blocked is True
    assert stored.executed is False
    assert stored.cancelled is False


def test_marking_an_unknown_entry_is_a_no_op():
    journal = JournalService()
    assert journal.mark_executed("does-not-exist") is None
    assert journal.mark_cancelled("does-not-exist") is None
    assert journal.mark_blocked("does-not-exist") is None


def test_marking_executed_without_a_price_keeps_the_stored_one():
    journal = JournalService()
    e = journal.add_entry(entry(price=99.0))

    journal.mark_executed(e.id)

    assert journal.get_entry(e.id).price == 99.0


def test_entries_come_back_in_insertion_order():
    """Ordered by rowid, not timestamp — the agent logs several trades inside
    one cycle and they can share a timestamp."""
    journal = JournalService()
    stamp = datetime.now(timezone.utc)
    for symbol in ["AAA", "BBB", "CCC"]:
        journal.add_entry(entry(symbol=symbol, timestamp=stamp))

    assert [e.symbol for e in journal.get_entries()] == ["AAA", "BBB", "CCC"]


def test_survives_a_restart(tmp_path):
    """The agent runs across days and restarts. If the journal doesn't persist,
    the P&L record and the behavior gap are lost."""
    db_path = str(tmp_path / "journal.db")

    first = JournalService(db_path=db_path)
    created = first.add_entry(
        entry(
            guardrail_result=flagged_result(),
            price=172.4,
            source=TradeSource.agent,
            signal_reason="momentum turned up",
        )
    )
    first.mark_blocked(created.id)
    first.close()

    second = JournalService(db_path=db_path)
    reloaded = second.get_entry(created.id)
    second.close()

    assert reloaded is not None
    assert reloaded.status == "blocked"
    assert reloaded.price == 172.4
    assert reloaded.source is TradeSource.agent
    assert reloaded.signal_reason == "momentum turned up"
    # The nested guardrail result round-trips through JSON intact.
    assert reloaded.guardrail_result.approved is False
    assert reloaded.guardrail_result.triggered_rules == ["oversized_position"]
    assert reloaded.guardrail_result.reference_price == 172.4


def test_separate_databases_do_not_share_state():
    a = JournalService()
    b = JournalService()
    a.add_entry(entry())

    assert len(a.get_entries()) == 1
    assert len(b.get_entries()) == 0
