"""
FastAPI app entrypoint. Wiring only — no business logic lives here.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import journal, trades, ui
from app.core.dependencies import get_alpaca_client
from app.schemas.account import AccountSnapshot
from app.services.alpaca_client import AlpacaClient

# Resolved from this file rather than the process working directory, so the
# app starts from anywhere instead of only from the backend/ folder.
APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await get_alpaca_client().aclose()


app = FastAPI(title="Alpaca Guardrail Agent", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

app.include_router(trades.router)
app.include_router(journal.router)
app.include_router(ui.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/account", response_model=AccountSnapshot)
async def get_account(client: AlpacaClient = Depends(get_alpaca_client)):
    return await client.get_account_snapshot()
