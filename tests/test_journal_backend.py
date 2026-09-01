"""
Storage backend selection.

The journal switches between SQLite and Postgres on the presence of
DATABASE_URL. That branch decides where a deployed agent's entire P&L record
lives, so it's worth testing directly — and it can be, without a database,
because the connection is made lazily.

Behavioural tests for the journal itself live in test_journal_service.py and run
against SQLite.
"""
import pytest

from app.services.journal_service import JournalService


class TestBackendSelection:
    def test_no_url_uses_sqlite(self):
        journal = JournalService()
        assert journal.dialect == "sqlite"

    def test_blank_url_uses_sqlite(self):
        """An unset platform variable arrives as an empty string, not as a
        missing one."""
        assert JournalService(db_url="").dialect == "sqlite"
        assert JournalService(db_url="   ").dialect == "sqlite"

    def test_url_selects_postgres(self):
        journal = JournalService(
            db_url="postgresql://u:p@ep-x-pooler.aws.neon.tech/db?sslmode=require"
        )
        assert journal.dialect == "postgres"

    def test_url_wins_over_a_local_path(self):
        """A deployed instance has both set — the URL is the deployed case."""
        journal = JournalService(
            db_url="postgresql://u:p@host/db", db_path="journal.db"
        )
        assert journal.dialect == "postgres"

    def test_url_is_whitespace_stripped(self):
        journal = JournalService(db_url="  postgresql://u:p@host/db  ")
        assert journal.db_path == "postgresql://u:p@host/db"


class TestLazyConnection:
    def test_postgres_backend_does_not_connect_on_construction(self):
        """A serverless cold start shouldn't pay for a database round trip until
        something actually reads or writes — and an unreachable host must not
        blow up at import/wiring time."""
        journal = JournalService(db_url="postgresql://nobody@127.0.0.1:1/none")
        assert journal.dialect == "postgres"  # constructed fine, never dialled

    def test_sqlite_backend_does_not_connect_on_construction(self, tmp_path):
        db_file = tmp_path / "lazy.db"
        JournalService(db_path=str(db_file))
        assert not db_file.exists()

    def test_first_use_creates_the_sqlite_file(self, tmp_path):
        db_file = tmp_path / "eager.db"
        journal = JournalService(db_path=str(db_file))
        journal.get_entries()
        journal.close()
        assert db_file.exists()


class TestDialectDetails:
    def test_placeholders_and_ordering_differ(self):
        """SQLite parameterises with `?` and orders by the implicit rowid;
        Postgres uses `%s` and an explicit BIGSERIAL, because it has no rowid."""
        sqlite = JournalService()._backend
        postgres = JournalService(db_url="postgresql://u@h/d")._backend

        assert (sqlite.placeholder, sqlite.order_by) == ("?", "rowid")
        assert (postgres.placeholder, postgres.order_by) == ("%s", "seq")

    def test_timestamp_adaptation_differs(self):
        """sqlite3's implicit datetime adapter is deprecated, so timestamps are
        stored as ISO text there and as native TIMESTAMPTZ on Postgres."""
        from datetime import datetime, timezone

        stamp = datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc)
        sqlite = JournalService()._backend
        postgres = JournalService(db_url="postgresql://u@h/d")._backend

        assert isinstance(sqlite.adapt_timestamp(stamp), str)
        assert postgres.adapt_timestamp(stamp) is stamp

    def test_sqlite_never_retries(self):
        """Reconnecting an in-memory database would silently discard it, and a
        local connection doesn't get dropped underneath us anyway."""
        assert JournalService()._backend.transient_errors == ()

    def test_postgres_retries_on_dropped_connections(self):
        psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")
        errors = JournalService(db_url="postgresql://u@h/d")._backend.transient_errors
        assert psycopg.OperationalError in errors
        assert psycopg.InterfaceError in errors
