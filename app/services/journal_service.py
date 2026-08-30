"""
Journal service — records trade decisions and computes summary stats.

Storage is in-memory and process-local: the journal resets when the server
restarts (including on uvicorn's --reload). Swapping in a database means
changing this one file plus its provider in core/dependencies.py.
"""
from app.schemas.trade import JournalEntry


class JournalService:
    def __init__(self):
        self._entries: list[JournalEntry] = []

    def add_entry(self, entry: JournalEntry) -> JournalEntry:
        """Record a proposed trade. Returns the stored entry so the caller
        has its id to update later."""
        self._entries.append(entry)
        return entry

    def get_entry(self, entry_id: str) -> JournalEntry | None:
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        return None

    def mark_executed(
        self,
        entry_id: str,
        *,
        price: float | None = None,
        was_overridden: bool = False,
    ) -> JournalEntry | None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        entry.executed = True
        entry.cancelled = False
        entry.was_overridden = was_overridden
        if price is not None:
            entry.price = price
        return entry

    def mark_cancelled(self, entry_id: str) -> JournalEntry | None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        entry.cancelled = True
        entry.executed = False
        return entry

    def get_entries(self) -> list[JournalEntry]:
        return list(self._entries)

    def get_summary(self) -> dict:
        """Counts by outcome.

        `guardrail_result` is optional on an entry, so every read of it is
        guarded — an entry with no recorded decision counts as neither
        flagged nor clean rather than crashing the endpoint.
        """
        entries = self._entries
        flagged = [e for e in entries if e.was_flagged]
        evaluated = [e for e in entries if e.guardrail_result is not None]

        return {
            "proposals": len(entries),
            "executed_trades": len([e for e in entries if e.executed]),
            "cancelled_trades": len([e for e in entries if e.cancelled]),
            "flagged_trades": len(flagged),
            "overridden_trades": len([e for e in flagged if e.executed and e.was_overridden]),
            "clean_trades": len(evaluated) - len(flagged),
        }
