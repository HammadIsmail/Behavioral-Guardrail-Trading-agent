"""
Server-rendered pages (Jinja2 + htmx + Alpine).

Six pages, each a thin assembly of services:

  /            dashboard — account, agent, signals, order ticket
  /movers      what's moving, plus current positions
  /analytics   equity curve and what the agent has been doing
  /decisions   what the guardrail and your selling actually cost you
  /chat        natural-language questions about your own history
  /settings    auto-trade switch, strategy and rule reference

`/fragments/*` returns HTML partials for htmx to swap in. Kept visibly distinct
from the JSON API in api/trades.py, api/journal.py, api/agent.py and friends.

No business logic here: parsing lives in services/trade_parser.py, the decision
in GuardrailService, the strategy in services/strategy.py, the wording in
ExplainerService and ChatService.
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_agent_service,
    get_agent_for_user,
    get_alpaca_client,
    get_behavior_gap_service,
    get_chat_service,
    get_explainer_service,
    get_guardrail_service,
    get_journal_service,
    get_movers_service,
    get_strategy_service,
    get_current_user_optional,
    get_current_user,
    get_user_service,
    get_user_agent_manager,
    get_journal_for_user,
    get_behavior_gap_for_user,
    get_alpaca_for_user_optional,
)
from app.schemas.user import UserSettings, UserInDB
from app.services.user_service import UserService
from app.services.user_agent_manager import UserAgentManager
from app.schemas.market import EquityPoint, PortfolioHistory
from app.schemas.trade import JournalEntry, OrderSide, TradeProposal, TradeSource
from app.services.agent import AgentService
from app.services.alpaca_client import AlpacaClient
from app.services.behavior_gap import BehaviorGapService
from app.services.chat import ChatService, build_context
from app.services.explainer import ExplainerService
from app.services.guardrail_rules import ALL_RULES
from app.services.guardrail_service import GuardrailService
from app.services.journal_service import JournalService
from app.services.movers import MoversService
from app.services.strategy import StrategyService
from app.services.trade_parser import parse_trade_message

router = APIRouter(tags=["ui"])

# Resolved from this file, not the working directory, so the app renders
# correctly regardless of where uvicorn was launched from.
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

CHAT_SUGGESTIONS = [
    "How am I doing overall?",
    "What has the guardrail stopped, and why?",
    "Is my selling costing me money?",
    "Which bias do I show most?",
    "What did the agent do today?",
]

# Descriptions live here rather than on the rule classes: the rules stay pure
# logic, and this is presentation.
RULE_COPY = {
    "oversized_position": ("A buy would put too much of the portfolio into one trade", "> 15% of portfolio"),
    "overexposure": ("A buy would put more capital to work than the portfolio is worth", "> 100% deployed"),
    "revenge_trade": ("A buy lands shortly after a sell", "any sell in 30 min"),
    "overtrading": ("Too many fills in a short window", "5+ in an hour"),
}


async def _equity(alpaca: AlpacaClient, period: str = "1M") -> PortfolioHistory:
    """Equity curve, tolerant of a missing feed — a chart is not worth failing a
    page load over."""
    payload = await alpaca.get_portfolio_history(period=period, timeframe="1D")

    stamps = payload.get("timestamp") or []
    equities = payload.get("equity") or []
    points: list[EquityPoint] = []
    for index, stamp in enumerate(stamps):
        value = equities[index] if index < len(equities) else None
        if value is None:
            continue  # Alpaca pads the series outside market hours
        points.append(
            EquityPoint(
                at=datetime.fromtimestamp(int(stamp), tz=timezone.utc),
                equity=float(value),
                profit_loss=0.0,
                profit_loss_pct=0.0,
            )
        )

    return PortfolioHistory(
        points=points, base_value=float(payload.get("base_value") or 0.0)
    )


def _deployed_pct(account) -> float:
    if account.portfolio_value <= 0:
        return 0.0
    invested = sum(abs(p.market_value) for p in account.positions)
    return invested / account.portfolio_value * 100


def _mask_key(value: str) -> str:
    if not value:
        return ""
    return f"{value[:4]}…{value[-4:]}" if len(value) > 8 else "••••"


def _unwrap_or_keep(submitted: str, stored: str, has_stored: bool) -> str:
    """A password input can't show the real secret, so the form may resubmit a
    mask or a blank. Blank -> keep existing; otherwise treat as a new value."""
    value = (submitted or "").strip()
    if value == "" or "…" in value:
        return stored
    return value


# =========================================================================
# Pages
# =========================================================================


@router.get("/", response_class=HTMLResponse)
async def page_dashboard(
    request: Request,
    user: UserInDB | None = Depends(get_current_user_optional),
    agent: AgentService = Depends(get_agent_service),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    # When the user hasn't stored keys we render the page immediately with a
    # connect prompt; KPIs and the equity chart lazy-load and stand down.
    has_keys = bool(
        user.settings.alpaca_api_key and user.settings.alpaca_secret_key
    )
    return templates.TemplateResponse(
        "pages/dashboard.html",
        {
            "request": request,
            "active": "dashboard",
            "agent": agent.status,
            "user": user,
            "has_alpaca": has_keys,
        },
    )


@router.get("/fragments/dashboard/kpis", response_class=HTMLResponse)
async def fragment_dashboard_kpis(
    request: Request,
    alpaca: AlpacaClient | None = Depends(get_alpaca_for_user_optional),
    journal: JournalService = Depends(get_journal_for_user),
    behavior_gap: BehaviorGapService = Depends(get_behavior_gap_for_user),
):
    if alpaca is None:
        return HTMLResponse("")
    account = await alpaca.get_account_snapshot()
    entries = journal.get_entries()
    gap, impact = await behavior_gap.compute_all(entries)
    return templates.TemplateResponse(
        "partials/dashboard_kpis.html",
        {
            "request": request,
            "account": account,
            "gap": gap,
            "impact": impact,
            "equity": await _equity(alpaca),
            "deployed_pct": _deployed_pct(account),
        },
    )


@router.get("/fragments/dashboard/equity", response_class=HTMLResponse)
async def fragment_dashboard_equity(
    request: Request,
    alpaca: AlpacaClient | None = Depends(get_alpaca_for_user_optional),
):
    if alpaca is None:
        return HTMLResponse("")
    return templates.TemplateResponse(
        "partials/equity_chart.html",
        {"request": request, "equity": await _equity(alpaca)},
    )


@router.get("/movers", response_class=HTMLResponse)
async def page_movers(
    request: Request,
    user: UserInDB | None = Depends(get_current_user_optional),
    agent: AgentService = Depends(get_agent_service),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "pages/movers.html",
        {
            "request": request,
            "active": "movers",
            "agent": agent.status,
            "user": user,
        },
    )


@router.get("/analytics", response_class=HTMLResponse)
async def page_analytics(
    request: Request,
    user: UserInDB | None = Depends(get_current_user_optional),
    period: str = Query("1M", pattern=r"^(1W|1M|3M|1A)$"),
    agent: AgentService = Depends(get_agent_service),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "pages/analytics.html",
        {
            "request": request,
            "active": "analytics",
            "agent": agent.status,
            "period": period,
            "user": user,
        },
    )


@router.get("/decisions", response_class=HTMLResponse)
async def page_decisions(
    request: Request,
    user: UserInDB | None = Depends(get_current_user_optional),
    agent: AgentService = Depends(get_agent_service),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "pages/decisions.html",
        {
            "request": request,
            "active": "decisions",
            "agent": agent.status,
            "user": user,
        },
    )


@router.get("/chat", response_class=HTMLResponse)
async def page_chat(
    request: Request,
    user: UserInDB | None = Depends(get_current_user_optional),
    journal: JournalService = Depends(get_journal_service),
    agent: AgentService = Depends(get_agent_service),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "pages/chat.html",
        {
            "request": request,
            "active": "chat",
            "agent": agent.status,
            "summary": journal.get_summary(),
            "suggestions": CHAT_SUGGESTIONS,
            "user": user,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def page_settings(
    request: Request,
    user: UserInDB | None = Depends(get_current_user_optional),
    journal: JournalService = Depends(get_journal_service),
    agent: AgentService = Depends(get_agent_for_user),
    strategy: StrategyService = Depends(get_strategy_service),
    settings: Settings = Depends(get_settings),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    rules = [
        {
            "name": rule.name,
            "description": RULE_COPY.get(rule.name, ("", ""))[0],
            "threshold": RULE_COPY.get(rule.name, ("", ""))[1],
        }
        for rule in ALL_RULES
    ]
    user_settings = user.settings
    api_key = user_settings.alpaca_api_key or ""
    return templates.TemplateResponse(
        "pages/settings.html",
        {
            "request": request,
            "active": "settings",
            "agent": agent.status,
            "strategy": strategy.strategy,
            "max_per_cycle": settings.agent_max_trades_per_cycle,
            "rules": rules,
            "groq_configured": bool(settings.groq_api_key),
            "journal_dialect": journal.dialect,
            "user": user,
            "alpaca_has_keys": bool(api_key and user_settings.alpaca_secret_key),
            "alpaca_api_hint": _mask_key(api_key),
            "alpaca_secret_hint": _mask_key(user_settings.alpaca_secret_key),
            "saved": False,
        },
    )


# =========================================================================
# Fragments — agent
# =========================================================================


@router.get("/fragments/agent", response_class=HTMLResponse)
async def fragment_agent(
    request: Request, agent: AgentService = Depends(get_agent_service)
):
    return templates.TemplateResponse(
        "partials/agent_panel.html", {"request": request, "agent": agent.status}
    )


@router.post("/fragments/agent/run-once", response_class=HTMLResponse)
async def fragment_agent_run_once(
    request: Request, agent: AgentService = Depends(get_agent_service)
):
    """Run one cycle on demand. Same guardrail, same journal — this is the
    scheduled loop's body, not a shortcut around it."""
    await agent.run_cycle()
    response = templates.TemplateResponse(
        "partials/agent_panel.html", {"request": request, "agent": agent.status}
    )
    response.headers["HX-Trigger"] = "journalUpdated"
    return response


@router.post("/fragments/agent/auto-trade", response_class=HTMLResponse)
async def fragment_auto_trade(
    request: Request,
    enabled: bool,
    agent: AgentService = Depends(get_agent_service),
):
    await agent.set_enabled(enabled)
    return templates.TemplateResponse(
        "partials/auto_trade.html", {"request": request, "agent": agent.status}
    )


@router.get("/fragments/signals", response_class=HTMLResponse)
async def fragment_signals(
    request: Request, strategy: StrategyService = Depends(get_strategy_service)
):
    """What the strategy wants right now. Read-only — nothing is traded."""
    signals, diagnostics = await strategy.generate()
    return templates.TemplateResponse(
        "partials/signals.html",
        {"request": request, "signals": signals, "diagnostics": diagnostics},
    )


# =========================================================================
# Fragments — chat
# =========================================================================


@router.post("/fragments/chat", response_class=HTMLResponse)
async def fragment_chat(
    request: Request,
    question: str = Form(...),
    journal: JournalService = Depends(get_journal_service),
    behavior_gap: BehaviorGapService = Depends(get_behavior_gap_service),
    chat: ChatService = Depends(get_chat_service),
):
    entries = journal.get_entries()
    gap, impact = await behavior_gap.compute_all(entries)
    context = build_context(entries, journal.get_summary(), gap, impact)
    reply = await chat.answer(question.strip(), context)
    return templates.TemplateResponse(
        "partials/chat_message.html", {"request": request, "reply": reply}
    )


# =========================================================================
# Fragments — manual trading
# =========================================================================


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
        # Not a flagged trade — we simply couldn't read it. Its own state, so the
        # user isn't offered a "proceed anyway" for a trade never understood.
        return templates.TemplateResponse(
            "partials/parse_error.html", {"request": request, "error": str(e)}
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
            source=TradeSource.user,
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

    The guardrail runs here, on the path that actually submits the order, rather
    than trusting the verdict from the propose step. The form is client-supplied
    and this endpoint is reachable on its own, so a trade can't reach Alpaca
    unchecked, and `was_overridden` is derived from the fresh decision rather
    than a posted field.
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
            "partials/parse_error.html", {"request": request, "error": str(e)}
        )

    was_overridden = not result.approved and wants_override

    entry = journal.mark_executed(
        journal_entry_id, price=result.reference_price, was_overridden=was_overridden
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
                source=TradeSource.user,
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
    """Record that the user backed off after being flagged — the outcome the whole
    product is trying to produce, so it has to reach the journal."""
    if journal_entry_id:
        journal.mark_cancelled(journal_entry_id)

    response = templates.TemplateResponse(
        "partials/execution_result.html",
        {"request": request, "executed": False, "cancelled": True},
    )
    response.headers["HX-Trigger"] = "journalUpdated"
    return response


# =========================================================================
# Fragments — journal
# =========================================================================


@router.get("/fragments/journal", response_class=HTMLResponse)
async def fragment_journal(
    request: Request,
    limit: int = Query(0, ge=0, le=500),
    journal: JournalService = Depends(get_journal_service),
):
    entries = journal.get_entries()
    shown = entries[-limit:] if limit else entries
    return templates.TemplateResponse(
        "partials/journal_list.html",
        {
            "request": request,
            "entries": shown,
            "summary": journal.get_summary() if not limit else None,
        },
    )


@router.get("/fragments/movers", response_class=HTMLResponse)
async def fragment_movers(
    request: Request,
    side: str = Query("gainers", pattern=r"^(gainers|losers)$"),
    movers: MoversService = Depends(get_movers_service),
):
    snapshot = await movers.get_movers(top=8)
    rows = snapshot.gainers if side == "gainers" else snapshot.losers
    return templates.TemplateResponse(
        "partials/movers_table.html", {"request": request, "rows": rows}
    )


@router.post("/fragments/settings/alpaca", response_class=HTMLResponse)
async def fragment_save_alpaca(
    request: Request,
    alpaca_api_key: str = Form(""),
    alpaca_secret_key: str = Form(""),
    user: UserInDB = Depends(get_current_user),
    manager: UserAgentManager = Depends(get_user_agent_manager),
):
    """Persist a user's own paper-trading credentials.

    If the user submits a masked value (a field they left untouched), keep the
    stored secret rather than overwriting it with the mask.
    """
    current = user.settings
    new_settings = UserSettings(
        alpaca_api_key=_unwrap_or_keep(
            alpaca_api_key, current.alpaca_api_key, current.alpaca_secret_key is not None
        ),
        alpaca_secret_key=_unwrap_or_keep(
            alpaca_secret_key, current.alpaca_secret_key, True
        ),
        agent_interval_seconds=current.agent_interval_seconds,
        agent_enabled=current.agent_enabled,
    )
    manager.update_settings(user.id, new_settings)

    has_keys = bool(new_settings.alpaca_api_key and new_settings.alpaca_secret_key)
    return templates.TemplateResponse(
        "partials/alpaca_connection.html",
        {
            "request": request,
            "alpaca_has_keys": has_keys,
            "alpaca_api_hint": _mask_key(new_settings.alpaca_api_key),
            "alpaca_secret_hint": _mask_key(new_settings.alpaca_secret_key),
            "saved": True,
        },
    )


# =========================================================================
# Fragments — lazy page bodies
# =========================================================================


@router.get("/fragments/movers/board", response_class=HTMLResponse)
async def fragment_movers_board(
    request: Request,
    movers: MoversService = Depends(get_movers_service),
):
    snapshot = await movers.get_movers(top=8)
    return templates.TemplateResponse(
        "partials/movers_board.html", {"request": request, "movers": snapshot}
    )


@router.get("/fragments/movers/positions", response_class=HTMLResponse)
async def fragment_movers_positions(
    request: Request,
    alpaca: AlpacaClient = Depends(get_alpaca_client),
):
    account = await alpaca.get_account_snapshot()
    return templates.TemplateResponse(
        "partials/movers_positions.html", {"request": request, "account": account}
    )


@router.get("/fragments/analytics", response_class=HTMLResponse)
async def fragment_analytics(
    request: Request,
    period: str = Query("1M", pattern=r"^(1W|1M|3M|1A)$"),
    alpaca: AlpacaClient = Depends(get_alpaca_client),
    journal: JournalService = Depends(get_journal_service),
    behavior_gap: BehaviorGapService = Depends(get_behavior_gap_service),
):
    entries = journal.get_entries()
    gap, impact = await behavior_gap.compute_all(entries)

    by_symbol: dict[str, dict] = {}
    for entry in entries:
        row = by_symbol.setdefault(
            entry.symbol.upper(), {"total": 0, "executed": 0, "blocked": 0}
        )
        row["total"] += 1
        if entry.executed:
            row["executed"] += 1
        if entry.blocked:
            row["blocked"] += 1
    by_symbol = dict(
        sorted(by_symbol.items(), key=lambda kv: kv[1]["total"], reverse=True)[:10]
    )

    return templates.TemplateResponse(
        "partials/analytics.html",
        {
            "request": request,
            "account": await alpaca.get_account_snapshot(),
            "gap": gap,
            "impact": impact,
            "summary": journal.get_summary(),
            "equity": await _equity(alpaca, period=period),
            "period": period,
            "by_symbol": by_symbol,
        },
    )


@router.get("/fragments/decisions", response_class=HTMLResponse)
async def fragment_decisions(
    request: Request,
    journal: JournalService = Depends(get_journal_service),
    behavior_gap: BehaviorGapService = Depends(get_behavior_gap_service),
):
    entries = journal.get_entries()
    gap, impact = await behavior_gap.compute_all(entries)
    return templates.TemplateResponse(
        "partials/decisions_cards.html",
        {
            "request": request,
            "gap": gap,
            "impact": impact,
            "summary": journal.get_summary(),
        },
    )


@router.post("/fragments/settings/agent-interval", response_class=HTMLResponse)
async def fragment_agent_interval(
    request: Request,
    interval_minutes: int = Form(...),
    user: UserInDB = Depends(get_current_user),
    manager: UserAgentManager = Depends(get_user_agent_manager),
):
    if interval_minutes < 1:
        return HTMLResponse("Interval must be at least 1 minute", status_code=400)
    interval_seconds = interval_minutes * 60
    # Get current settings from user object
    new_settings = user.settings.model_copy()
    new_settings.agent_interval_seconds = interval_seconds
    manager.update_settings(user.id, new_settings)
    return HTMLResponse(f"Updated to {interval_minutes} min")
