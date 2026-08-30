"""
Server-rendered dashboard (Jinja2 + htmx).

The dashboard is the app root (`GET /`). The other routes here return HTML
fragments for htmx to swap in, and live under `/fragments/` to keep them
visibly distinct from the JSON API in api/trades.py and api/journal.py.

These routes are deliberately thin: parsing lives in
services/trade_parser.py, the decision in GuardrailService, the wording in
ExplainerService.
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.dependencies import (
    get_alpaca_client,
    get_behavior_gap_service,
    get_explainer_service,
    get_guardrail_service,
    get_journal_service,
)
from app.schemas.trade import JournalEntry, OrderSide, TradeProposal
from app.services.alpaca_client import AlpacaClient
from app.services.behavior_gap import BehaviorGapService
from app.services.explainer import ExplainerService
from app.services.guardrail_service import GuardrailService
from app.services.journal_service import JournalService
from app.services.trade_parser import parse_trade_message

router = APIRouter(tags=["ui"])

# Resolved from this file, not the working directory, so the app renders
# correctly regardless of where uvicorn was launched from.
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    alpaca: AlpacaClient = Depends(get_alpaca_client),
    journal: JournalService = Depends(get_journal_service),
    behavior_gap: BehaviorGapService = Depends(get_behavior_gap_service),
):
    account = await alpaca.get_account_snapshot()
    entries = journal.get_entries()
    gap = await behavior_gap.compute(entries)
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "account": account, "entries": entries, "gap": gap},
    )


@router.post("/fragments/trades/propose", response_class=HTMLResponse)
async def fragment_propose_trade(
    request: Request,
    message: str = Form(...),
    guardrail: GuardrailService = Depends(get_guardrail_service),
    explainer: ExplainerService = Depends(get_explainer_service),
    journal: JournalService = Depends(get_journal_service),
):
    try:
        proposal = parse_trade_message(message)
    except ValueError as e:
        # Not a flagged trade — we simply couldn't read it. Rendered as its
        # own state so the user isn't offered a "proceed anyway" button for
        # a trade that was never understood in the first place.
        return templates.TemplateResponse(
            "partials/parse_error.html",
            {"request": request, "error": str(e)},
        )

    result = await guardrail.evaluate(proposal)
    result.explanation = await explainer.explain(result)

    entry = journal.add_entry(
        JournalEntry(
            timestamp=datetime.now(timezone.utc),
            symbol=proposal.symbol,
            qty=proposal.qty,
            side=proposal.side,
            guardrail_result=result,
            price=result.reference_price,
        )
    )

    response = templates.TemplateResponse(
        "partials/trade_result.html",
        {
            "request": request,
            "result": result,
            "proposal": proposal,
            "journal_entry_id": entry.id,
        },
    )
    response.headers["HX-Trigger"] = "journalUpdated"
    return response


@router.post("/fragments/trades/execute", response_class=HTMLResponse)
async def fragment_execute_trade(
    request: Request,
    symbol: str = Form(...),
    qty: float = Form(...),
    side: str = Form(...),
    override: str = Form("false"),
    journal_entry_id: str = Form(""),
    guardrail: GuardrailService = Depends(get_guardrail_service),
    alpaca: AlpacaClient = Depends(get_alpaca_client),
    journal: JournalService = Depends(get_journal_service),
):
    """Place the order — after re-running the guardrail.

    The guardrail runs here, on the path that actually submits the order,
    rather than trusting the verdict from the propose step. The form is
    client-supplied and this endpoint is reachable on its own, so a trade
    can't reach Alpaca without being checked, and `was_overridden` is
    derived from the fresh decision instead of a posted field.
    """
    try:
        proposal = TradeProposal(symbol=symbol.upper(), qty=qty, side=OrderSide(side))
    except ValueError:
        return templates.TemplateResponse(
            "partials/parse_error.html",
            {"request": request, "error": "That trade was malformed — try again."},
        )

    wants_override = override.strip().lower() in {"true", "1", "yes", "on"}
    result = await guardrail.evaluate(proposal)

    # Flagged and not explicitly overridden: add friction, don't execute.
    if not result.approved and not wants_override:
        return templates.TemplateResponse(
            "partials/trade_result.html",
            {
                "request": request,
                "result": result,
                "proposal": proposal,
                "journal_entry_id": journal_entry_id,
            },
        )

    try:
        order = await alpaca.submit_order(proposal)
    except ValueError as e:
        return templates.TemplateResponse(
            "partials/parse_error.html",
            {"request": request, "error": str(e)},
        )

    was_overridden = not result.approved and wants_override

    entry = journal.mark_executed(
        journal_entry_id,
        price=result.reference_price,
        was_overridden=was_overridden,
    )
    if entry is None:
        journal.add_entry(
            JournalEntry(
                timestamp=datetime.now(timezone.utc),
                symbol=proposal.symbol,
                qty=proposal.qty,
                side=proposal.side,
                guardrail_result=result,
                was_overridden=was_overridden,
                executed=True,
                price=result.reference_price,
            )
        )

    response = templates.TemplateResponse(
        "partials/execution_result.html",
        {
            "request": request,
            "executed": True,
            "cancelled": False,
            "order": order,
            "was_overridden": was_overridden,
        },
    )
    response.headers["HX-Trigger"] = "journalUpdated"
    return response


@router.post("/fragments/trades/cancel", response_class=HTMLResponse)
async def fragment_cancel_trade(
    request: Request,
    journal_entry_id: str = Form(""),
    journal: JournalService = Depends(get_journal_service),
):
    """Record that the user backed off after being flagged.

    This is the outcome the whole product is trying to produce, so it has to
    actually reach the journal — the template has always claimed it was
    logged.
    """
    if journal_entry_id:
        journal.mark_cancelled(journal_entry_id)

    response = templates.TemplateResponse(
        "partials/execution_result.html",
        {"request": request, "executed": False, "cancelled": True},
    )
    response.headers["HX-Trigger"] = "journalUpdated"
    return response


@router.get("/fragments/journal", response_class=HTMLResponse)
async def fragment_journal(
    request: Request,
    journal: JournalService = Depends(get_journal_service),
    behavior_gap: BehaviorGapService = Depends(get_behavior_gap_service),
):
    entries = journal.get_entries()
    return templates.TemplateResponse(
        "partials/journal_list.html",
        {"request": request, "entries": entries, "gap": await behavior_gap.compute(entries)},
    )
