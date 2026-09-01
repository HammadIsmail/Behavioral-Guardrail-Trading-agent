from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Alpaca paper trading credentials
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"

    # Groq (LLM for plain-language explanations)
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # App
    app_env: str = "development"

    # JWT
    secret_key: str = "change_this_in_production"
    algorithm: str = "HS256"

    # Journal persistence. The autonomous agent runs for days, so the journal
    # is the P&L record and has to survive a restart.
    #
    # DATABASE_URL set   -> Postgres (what a serverless platform injects)
    # DATABASE_URL unset -> SQLite at journal_db_path (local default)
    #
    # Keyed off the URL's presence rather than app_env so a deploy needs no
    # extra configuration and a local run needs none at all.
    database_url: str = ""
    journal_db_path: str = "journal.db"

    # --- Autonomous agent ---
    # Whether the background trading loop runs. Paper trading only, and
    # gated on market hours regardless.
    agent_enabled: bool = True
    agent_interval_seconds: int = 900          # 15 minutes
    agent_max_trades_per_cycle: int = 3
    agent_universe: str = "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AMD,NFLX,JPM"

    # --- Strategy ---
    strategy_short_window: int = 5
    strategy_long_window: int = 20
    # Base target size for a new position, as a fraction of portfolio value.
    strategy_base_position_pct: float = 0.08
    # Strong signals scale the position up to this multiple of the base. This
    # is deliberately allowed to exceed the guardrail's 15% ceiling — that's
    # how conviction scaling gets caught rather than silently obeyed.
    strategy_max_conviction_multiple: float = 3.0
    # Total capital the strategy will put to work, as a fraction of portfolio
    # value. 1.0 = no margin. Kept in step with
    # OverexposureRule.MAX_TOTAL_EXPOSURE_PCT, which is the actual authority.
    strategy_max_total_exposure_pct: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def universe(self) -> list[str]:
        return [s.strip().upper() for s in self.agent_universe.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    """
    Cached so we parse .env once, not on every request.
    FastAPI routes will pull this via Depends(get_settings).
    """
    return Settings()
