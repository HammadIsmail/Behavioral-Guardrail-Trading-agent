"""
Agent routes — inspect and drive the autonomous trading loop.
"""
from fastapi import APIRouter, Depends

from app.core.dependencies import get_agent_for_user, get_strategy_for_user, get_current_user
from app.schemas.strategy import (
    AgentCycleResult,
    AgentStatus,
    StrategyDiagnostics,
    StrategySignal,
)
from app.services.agent import AgentService
from app.services.strategy import StrategyService

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/status", response_model=AgentStatus)
async def get_status(agent: AgentService = Depends(get_agent_for_user)):
    """Whether the loop is alive, what it has done, and what it decided last."""
    return agent.status


@router.post("/run-once", response_model=AgentCycleResult)
async def run_once(agent: AgentService = Depends(get_agent_for_user)):
    """Run one cycle immediately instead of waiting for the interval.

    Exists for demos and for the CLI — the loop is otherwise self-driving.
    Trades placed here go through exactly the same guardrail.
    """
    return await agent.run_cycle()


@router.post("/start")
async def start(agent: AgentService = Depends(get_agent_for_user)):
    started = agent.start()
    return {"started": started, "loop_running": agent.loop_running}


@router.post("/stop")
async def stop(agent: AgentService = Depends(get_agent_for_user)):
    await agent.stop()
    return {"loop_running": agent.loop_running}


@router.post("/auto-trade")
async def set_auto_trade(
    enabled: bool, agent: AgentService = Depends(get_agent_for_user)
):
    """Turn auto-trade on or off.

    Off stops the loop but leaves the journal and all history intact, so the
    behavior gap and guardrail impact keep reading correctly while the agent
    sits idle.
    """
    await agent.set_enabled(enabled)
    return {"enabled": agent.enabled, "loop_running": agent.loop_running}


@router.get("/signals", response_model=list[StrategySignal])
async def get_signals(strategy: StrategyService = Depends(get_strategy_for_user)):
    """What the strategy wants right now, without trading it.

    Read-only: this does not touch the guardrail or Alpaca's order endpoint,
    so it's safe to poll while explaining the strategy.
    """
    signals, _ = await strategy.generate()
    return signals


@router.get("/diagnostics", response_model=list[StrategyDiagnostics])
async def get_diagnostics(strategy: StrategyService = Depends(get_strategy_for_user)):
    """Every symbol the strategy looked at and what it concluded — including
    the holds. Shows the agent reasoning rather than just acting."""
    _, diagnostics = await strategy.generate()
    return diagnostics
