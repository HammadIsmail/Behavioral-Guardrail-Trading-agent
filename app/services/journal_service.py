"""
Journal service — records trade decisions and computes summary stats.

Two storage backends behind one interface:

  SQLite    — the default, and what runs locally. Zero setup.
  Postgres  — used whenever DATABASE_URL is set, which is what a serverless
              platform (Neon, Vercel, Render, Fly) injects for you. So the
              switch needs no local configuration: unset means local.

The journal is the P&L record. The autonomous agent runs across days and
restarts, so losing it loses the behavior gap, the guardrail counterfactual and
the demo — which is why it can't live in memory (ADR-018) and why a serverless
deployment can't keep it in a file on an ephemeral disk (ADR-022).

Storage is deliberately contained here. Nothing outside this file knows the
journal is a database, let alone which one.
"""
import json
import sqlite3
import threading
from datetime import datetime

from app.schemas.trade import GuardrailResult, JournalEntry, OrderSide, TradeSource

# Column order is shared by both dialects; only the placeholder style and the
# insertion-order column differ.
_COLUMNS = [
    "id",
    "timestamp",
    "symbol",
    "qty",
    "side",
    "guardrail_result",
    "was_overridden",
    "executed",
    "cancelled",
    "blocked",
    "price",
    "source",
    "signal_reason",
    "user_id",
]
_COLUMN_SQL = ", ".join(_COLUMNS)

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_entries (
    id               TEXT PRIMARY KEY,
    timestamp        TEXT    NOT NULL,
    symbol           TEXT    NOT NULL,
    qty              REAL    NOT NULL,
    side             TEXT    NOT NULL,
    guardrail_result TEXT,
    was_overridden   INTEGER NOT NULL DEFAULT 0,
    executed         INTEGER NOT NULL DEFAULT 0,
    cancelled        INTEGER NOT NULL DEFAULT 0,
    blocked          INTEGER NOT NULL DEFAULT 0,
    price            REAL,
    source           TEXT    NOT NULL DEFAULT 'user',
    signal_reason    TEXT    NOT NULL DEFAULT '',
    user_id          TEXT    NOT NULL DEFAULT ''
)
"""

# Postgres has no implicit rowid, so insertion order needs an explicit sequence.
# guardrail_result stays TEXT rather than JSONB: nothing queries inside it, and
# TEXT keeps a single read path across both dialects.
_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_entries (
    seq              BIGSERIAL,
    id               TEXT PRIMARY KEY,
    timestamp        TIMESTAMPTZ      NOT NULL,
    symbol           TEXT             NOT NULL,
    qty              DOUBLE PRECISION NOT NULL,
    side             TEXT             NOT NULL,
    guardrail_result TEXT,
    was_overridden   BOOLEAN          NOT NULL DEFAULT FALSE,
    executed         BOOLEAN          NOT NULL DEFAULT FALSE,
    cancelled        BOOLEAN          NOT NULL DEFAULT FALSE,
    blocked          BOOLEAN          NOT NULL DEFAULT FALSE,
    price            DOUBLE PRECISION,
    source           TEXT             NOT NULL DEFAULT 'user',
    signal_reason    TEXT             NOT NULL DEFAULT '',
    user_id          TEXT             NOT NULL DEFAULT ''
)
"""


class _SqliteBackend:
    """Local, file- or memory-backed."""

    dialect = "sqlite"
    placeholder = "?"
    order_by = "rowid"
    schema = _SQLITE_SCHEMA
    # A local connection doesn't get dropped underneath us, and reconnecting an
    # in-memory database would silently discard everything in it.
    transient_errors: tuple = ()

    def __init__(self, db_path: str):
        self.target = db_path

    def connect(self):
        conn = sqlite3.connect(self.target, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def migrate(self, conn):
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(journal_entries)").fetchall()
        }
        if "user_id" not in cols:
            conn.execute(
                "ALTER TABLE journal_entries ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
            )
        conn.commit()

    @staticmethod
    def adapt_timestamp(value: datetime):
        # sqlite3's implicit datetime adapter is deprecated, so store ISO text.
        return value.isoformat()


class _PostgresBackend:
    """Serverless-friendly, driven by DATABASE_URL."""

    dialect = "postgres"
    placeholder = "%s"
    order_by = "seq"
    schema = _POSTGRES_SCHEMA

    def __init__(self, db_url: str):
        self.target = db_url
        self._driver = None

    def _psycopg(self):
        # Imported lazily so a local SQLite run doesn't require the driver.
        if self._driver is None:
            import psycopg

            self._driver = psycopg
        return self._driver

    @property
    def transient_errors(self) -> tuple:
        psycopg = self._psycopg()
        return (psycopg.OperationalError, psycopg.InterfaceError)

    def connect(self):
        psycopg = self._psycopg()
        from psycopg.rows import dict_row

        return psycopg.connect(self.target, row_factory=dict_row)

    def migrate(self, conn):
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'journal_entries'"
        ).fetchall()
        cols = {row["column_name"] for row in rows}
        if "user_id" not in cols:
            conn.execute(
                "ALTER TABLE journal_entries ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
            )
        conn.commit()

    @staticmethod
    def adapt_timestamp(value: datetime):
        return value


class JournalService:
    def __init__(self, db_url: str = "", db_path: str = ":memory:", user_id: str = ""):
        """`db_url` wins when set — that's the deployed case. Otherwise SQLite
        at `db_path`, defaulting to in-memory so tests get a clean journal
        without touching disk."""
        self._backend = (
            _PostgresBackend(db_url.strip())
            if db_url and db_url.strip()
            else _SqliteBackend(db_path)
        )
        self._conn = None
        # Both drivers hand back connections that aren't safe for concurrent
        # use, and FastAPI may serve requests from different threads.
        self._lock = threading.Lock()
        self._user_id = user_id

    @property
    def dialect(self) -> str:
        return self._backend.dialect

    @property
    def db_path(self) -> str:
        """Where the journal lives: a URL under Postgres, a file path under
        SQLite."""
        return self._backend.target

    def close(self) -> None:
        with self._lock:
            self._reset()

    # ---------- connection handling ----------

    def _reset(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _connection(self):
        """Connected lazily: a serverless cold start shouldn't pay for a
        database round trip until something actually reads or writes."""
        if self._conn is None:
            self._conn = self._backend.connect()
            self._conn.execute(self._backend.schema)
            self._conn.commit()
            migrate = getattr(self._backend, "migrate", None)
            if migrate is not None:
                migrate(self._conn)
        return self._conn

    def _run(self, sql: str, params: tuple = (), *, mode: str = "exec"):
        with self._lock:
            try:
                return self._execute(sql, params, mode)
            except self._backend.transient_errors:
                # Serverless containers get their database connections dropped
                # between invocations. Reconnect once and retry rather than
                # failing a trade decision over a stale socket.
                self._reset()
                return self._execute(sql, params, mode)

    def _execute(self, sql: str, params: tuple, mode: str):
        conn = self._connection()
        cursor = conn.execute(sql, params)
        if mode == "one":
            row = cursor.fetchone()
            conn.commit()
            return row
        if mode == "all":
            rows = cursor.fetchall()
            conn.commit()
            return rows
        conn.commit()
        return cursor.rowcount

    # ---------- writes ----------

    def add_entry(self, entry: JournalEntry) -> JournalEntry:
        """Record a proposed trade. Returns the stored entry so the caller has
        its id to update later."""
        placeholders = ", ".join([self._backend.placeholder] * len(_COLUMNS))
        self._run(
            f"INSERT INTO journal_entries ({_COLUMN_SQL}) VALUES ({placeholders})",
            (
                entry.id,
                self._backend.adapt_timestamp(entry.timestamp),
                entry.symbol,
                float(entry.qty),
                entry.side.value,
                entry.guardrail_result.model_dump_json()
                if entry.guardrail_result
                else None,
                bool(entry.was_overridden),
                bool(entry.executed),
                bool(entry.cancelled),
                bool(entry.blocked),
                entry.price,
                entry.source.value,
                entry.signal_reason,
                self._user_id or entry.user_id,
            ),
        )
        return entry

    def mark_executed(
        self,
        entry_id: str,
        *,
        price: float | None = None,
        was_overridden: bool = False,
    ) -> JournalEntry | None:
        return self._update(
            entry_id,
            executed=True,
            cancelled=False,
            blocked=False,
            was_overridden=bool(was_overridden),
            price=price,
        )

    def mark_cancelled(self, entry_id: str) -> JournalEntry | None:
        return self._update(entry_id, cancelled=True, executed=False, blocked=False)

    def mark_blocked(self, entry_id: str) -> JournalEntry | None:
        """The agent proposed this, the guardrail flagged it, and the agent
        stood down. This is what the counterfactual is computed on."""
        return self._update(entry_id, blocked=True, executed=False, cancelled=False)

    def _update(self, entry_id: str, **fields) -> JournalEntry | None:
        # A None price means "leave whatever is stored" rather than "clear it".
        if fields.get("price", "keep") is None:
            fields.pop("price")

        ph = self._backend.placeholder
        assignments = ", ".join(f"{column} = {ph}" for column in fields)
        rowcount = self._run(
            f"UPDATE journal_entries SET {assignments} WHERE id = {ph}",
            (*fields.values(), entry_id),
        )
        if not rowcount:
            return None
        return self.get_entry(entry_id)

    # ---------- reads ----------

    def get_entry(self, entry_id: str) -> JournalEntry | None:
        sql = f"SELECT {_COLUMN_SQL} FROM journal_entries WHERE id = {self._backend.placeholder}"
        params = [entry_id]
        if self._user_id:
            sql += f" AND user_id = {self._backend.placeholder}"
            params.append(self._user_id)
        row = self._run(sql, tuple(params), mode="one")
        return _to_entry(row) if row else None

    def get_entries(self) -> list[JournalEntry]:
        """Oldest first. Ordered by the dialect's insertion-order column rather
        than by timestamp, because the agent can log several trades inside the
        same cycle."""
        sql = f"SELECT {_COLUMN_SQL} FROM journal_entries"
        params = ()
        if self._user_id:
            sql += f" WHERE user_id = {self._backend.placeholder}"
            params = (self._user_id,)
        sql += f" ORDER BY {self._backend.order_by}"
        rows = self._run(sql, params, mode="all")
        return [_to_entry(row) for row in rows]

    def get_summary(self) -> dict:
        """Counts by outcome.

        `guardrail_result` is nullable, so every read of it is guarded — an
        entry with no recorded decision counts as neither flagged nor clean
        rather than crashing the endpoint.
        """
        entries = self.get_entries()
        flagged = [e for e in entries if e.was_flagged]
        evaluated = [e for e in entries if e.guardrail_result is not None]
        by_agent = [e for e in entries if e.source is TradeSource.agent]

        return {
            "proposals": len(entries),
            "executed_trades": len([e for e in entries if e.executed]),
            "cancelled_trades": len([e for e in entries if e.cancelled]),
            "blocked_trades": len([e for e in entries if e.blocked]),
            "flagged_trades": len(flagged),
            "overridden_trades": len(
                [e for e in flagged if e.executed and e.was_overridden]
            ),
            "clean_trades": len(evaluated) - len(flagged),
            "agent_proposals": len(by_agent),
            "agent_executed": len([e for e in by_agent if e.executed]),
            "agent_blocked": len([e for e in by_agent if e.blocked]),
        }


def _parse_timestamp(value) -> datetime:
    """SQLite hands back ISO text; Postgres hands back a tz-aware datetime."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _to_entry(row) -> JournalEntry:
    raw_result = row["guardrail_result"]
    return JournalEntry(
        id=row["id"],
        timestamp=_parse_timestamp(row["timestamp"]),
        symbol=row["symbol"],
        qty=row["qty"],
        side=OrderSide(row["side"]),
        guardrail_result=(
            GuardrailResult.model_validate(json.loads(raw_result))
            if raw_result
            else None
        ),
        was_overridden=bool(row["was_overridden"]),
        executed=bool(row["executed"]),
        cancelled=bool(row["cancelled"]),
        blocked=bool(row["blocked"]),
        price=row["price"],
        source=TradeSource(row["source"]),
        signal_reason=row["signal_reason"],
        user_id=(row["user_id"] if "user_id" in row.keys() else ""),
    )
