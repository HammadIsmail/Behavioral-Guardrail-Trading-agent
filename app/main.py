"""
FastAPI app entrypoint. Wiring only — no business logic lives here.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import agent, auth, auth_ui, chat, journal, market, trades, ui, user
from app.core.dependencies import get_agent_service, get_alpaca_client
from app.schemas.account import AccountSnapshot
from app.services.alpaca_client import AlpacaClient

# Resolved from this file rather than the process working directory, so the
# app starts from anywhere instead of only from the backend/ folder.
APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    # For multi-user, we no longer start a single global agent.
    # Each user's agent is started on demand via /agent/start per user.
    # The global agent remains for backward compatibility but may be removed.
    # We keep the default agent start to avoid breaking existing behavior.
    agent_service = get_agent_service()
    agent_service.start()
    try:
        yield
    finally:
        await agent_service.stop()
        await get_alpaca_client().aclose()


app = FastAPI(title="Alpaca Guardrail Agent", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

app.include_router(auth.router)
app.include_router(auth_ui.router)
app.include_router(user.router)
app.include_router(trades.router)
app.include_router(journal.router)
app.include_router(agent.router)
app.include_router(market.router)
app.include_router(chat.router)
app.include_router(ui.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/account", response_model=AccountSnapshot)
async def get_account(client: AlpacaClient = Depends(get_alpaca_client)):
    return await client.get_account_snapshot()
