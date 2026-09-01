import json
import sqlite3
from datetime import datetime
from typing import Optional, List
from passlib.context import CryptContext
from jose import jwt
from app.schemas.user import UserCreate, UserInDB, UserSettings, UserPublic
from app.core.config import get_settings
from jose import jwt
from datetime import datetime, timedelta, timezone

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserService:
    def __init__(self, db_url: str = "", db_path: str = ":memory:"):
        self._backend = (
            _PostgresBackend(db_url.strip())
            if db_url and db_url.strip()
            else _SqliteBackend(db_path)
        )
        self._conn = None
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = self._connection()
            conn.execute(self._backend.user_table_schema)
            conn.commit()

    def _connection(self):
        if self._conn is None:
            self._conn = self._backend.connect()
        return self._conn

    def _reset(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except:
                pass
            self._conn = None

    def _execute(self, sql, params=(), mode="exec"):
        with self._lock:
            try:
                return self._run(sql, params, mode)
            except self._backend.transient_errors:
                self._reset()
                return self._run(sql, params, mode)

    def _run(self, sql, params, mode):
        conn = self._connection()
        if self._backend.placeholder != "?":
            sql = sql.replace("?", self._backend.placeholder)
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

    def create_user(self, user: UserCreate) -> UserInDB:
        hashed = pwd_context.hash(user.password)
        user_id = uuid.uuid4().hex
        settings_json = json.dumps(UserSettings().model_dump())
        sql = """
            INSERT INTO users (id, username, hashed_password, settings)
            VALUES (?, ?, ?, ?)
        """
        self._execute(sql, (user_id, user.username, hashed, settings_json))
        return UserInDB(id=user_id, username=user.username, hashed_password=hashed, settings=UserSettings())

    def get_user_by_username(self, username: str) -> Optional[UserInDB]:
        row = self._execute(
            "SELECT id, username, hashed_password, settings FROM users WHERE username = ?",
            (username,),
            mode="one"
        )
        if not row:
            return None
        settings = UserSettings.model_validate(json.loads(row["settings"]))
        return UserInDB(id=row["id"], username=row["username"], hashed_password=row["hashed_password"], settings=settings)

    def get_user_by_id(self, user_id: str) -> Optional[UserInDB]:
        row = self._execute(
            "SELECT id, username, hashed_password, settings FROM users WHERE id = ?",
            (user_id,),
            mode="one"
        )
        if not row:
            return None
        settings = UserSettings.model_validate(json.loads(row["settings"]))
        return UserInDB(id=row["id"], username=row["username"], hashed_password=row["hashed_password"], settings=settings)

    def update_settings(self, user_id: str, settings: UserSettings) -> Optional[UserInDB]:
        settings_json = json.dumps(settings.model_dump())
        rowcount = self._execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (settings_json, user_id)
        )
        if not rowcount:
            return None
        return self.get_user_by_id(user_id)

    def get_all_users(self) -> List[UserInDB]:
        rows = self._execute(
            "SELECT id, username, hashed_password, settings FROM users",
            mode="all"
        )
        users = []
        for row in rows:
            settings = UserSettings.model_validate(json.loads(row["settings"]))
            users.append(UserInDB(id=row["id"], username=row["username"], hashed_password=row["hashed_password"], settings=settings))
        return users

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)

    def create_access_token(self, user_id: str) -> str:
        settings = get_settings()
        expire = datetime.now(timezone.utc) + timedelta(days=7)
        to_encode = {"sub": user_id, "exp": expire}
        return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)

    def decode_token(self, token: str) -> Optional[str]:
        settings = get_settings()
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
            return payload.get("sub")
        except jwt.JWTError:
            return None

class _SqliteBackend:
    dialect = "sqlite"
    placeholder = "?"
    transient_errors = ()
    user_table_schema = """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            settings TEXT NOT NULL
        )
    """
    def __init__(self, db_path: str):
        self.target = db_path
    def connect(self):
        conn = sqlite3.connect(self.target, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

class _PostgresBackend:
    dialect = "postgres"
    placeholder = "%s"
    transient_errors = ()
    user_table_schema = """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            settings TEXT NOT NULL
        )
    """
    def __init__(self, db_url: str):
        self.target = db_url
        self._driver = None
    def _psycopg(self):
        if self._driver is None:
            import psycopg
            self._driver = psycopg
        return self._driver
    @property
    def transient_errors(self):
        psycopg = self._psycopg()
        return (psycopg.OperationalError, psycopg.InterfaceError)
    def connect(self):
        psycopg = self._psycopg()
        from psycopg.rows import dict_row
        return psycopg.connect(self.target, row_factory=dict_row)

import threading
import uuid