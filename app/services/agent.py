"""
The autonomous trading agent.

This is what makes the project an agent rather than an advisor: a background
loop that generates its own signals, runs each one through the same guardrail a
human would face, and either trades or stands down — with no human in the loop.

The design point worth understanding: **when there is nobody to ask, a flagged
trade is not executed.** A human at the dashboard gets a confirm/cancel choice,
because autonomy is theirs to keep. The agent gets no such latitude — it blocks
itself and records what it declined to do. Those blocked trades are the
counterfactual that `behavior_gap.compute_guardrail_impact` prices later, which
is how restraint becomes a number instead of a claim.
"""
import asyncio
from datetime import datetime, timezone

from app.core.config import Settings
from app.schemas.strategy import AgentCycleResult, AgentStatus
from app.schemas.trade import JournalEntry, TradeProposal, TradeSource
from app.services.alpaca_client import AlpacaClient
from app.services.explainer import ExplainerService
from app.services.guardrail_service import GuardrailService
from app.services.journal_service import JournalService
from app.services.strategy import StrategyService


class AgentService:
    def __init__(
        self,
        strategy: StrategyService,
        guardrail: GuardrailService,
        alpaca: AlpacaClient,
        journal: JournalService,
        explainer: ExplainerService,
        settings: Settings,
    ):
        self._strategy = strategy
        self._guardrail = guardrail
        self._alpaca = alpaca
        self._journal = journal
        self._explainer = explainer

        self._enabled = settings.agent_enabled
        self._interval = max(30, settings.agent_interval_seconds)
        self._max_per_cycle = max(1, settings.agent_max_trades_per_cycle)

        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

        self._cycles = 0
        self._total_proposed = 0
        self._total_executed = 0
        self._total_blocked = 0
        self._last_run_at: datetime | None = None
        self._last_error: str | None = None
        self._last_cycle: AgentCycleResult | None = None

    # ---------- lifecycle ----------

    @property
    def loop_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> bool:
        """Start the background loop. No-op if disabled or already running."""
        if not self._enabled or self.loop_running:
            return False
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="guardrail-agent")
        return True

    async def set_enabled(self, enabled: bool) -> bool:
        """Turn auto-trade on or off at runtime.

        `AGENT_ENABLED` in the environment sets the starting state; this is the
        user flipping it from the dashboard. Turning it off stops the loop but
        leaves the journal and all history intact, so the metrics keep reading
        correctly while the agent sits idle.
        """
        self._enabled = enabled
        if enabled:
            return self.start()
        await self.stop()
        return False

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_cycle()
            except Exception as e:  # a bad cycle must not kill the agent
                self._last_error = f"{type(e).__name__}: {e}"

            # Sleep, but wake immediately on shutdown.
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    # ---------- one pass ----------

    async def run_cycle(self) -> AgentCycleResult:
        """Generate signals, guardrail each one, trade or stand down.

        Safe to call directly — the dashboard and CLI both do, so a demo
        doesn't have to wait for the interval.
        """
        clock = await self._alpaca.get_clock()
        market_open = bool(clock.get("is_open"))

        result = AgentCycleResult(
            ran_at=datetime.now(timezone.utc), market_open=market_open
        )
        self._cycles += 1
        self._last_run_at = result.ran_at

        if not market_open:
            # Recorded as a completed cycle so the dashboard can show the agent
            # is alive and waiting rather than broken.
            self._last_cycle = result
            return result

        account = await self._alpaca.get_account_snapshot()
        signals, diagnostics = await self._strategy.generate(account)
        result.diagnostics = diagnostics
        result.signals_generated = len(signals)

        traded = 0
        for signal in signals:
            if traded >= self._max_per_cycle:
                result.skipped_cap += 1
                continue

            proposal = TradeProposal(
                symbol=signal.symbol, qty=signal.qty, side=signal.side
            )
            verdict = await self._guardrail.evaluate(proposal)

            # Only spend an LLM call on the interesting ones. A clean
            # autonomous trade needs no prose, and Groq's free tier is finite.
            if not verdict.approved:
                verdict.explanation = await self._explainer.explain(verdict)

            entry = self._journal.add_entry(
                JournalEntry(
                    timestamp=datetime.now(timezone.utc),
                    symbol=proposal.symbol,
                    qty=proposal.qty,
                    side=proposal.side,
                    guardrail_result=verdict,
                    price=verdict.reference_price or signal.price,
                    source=TradeSource.agent,
                    signal_reason=signal.reason,
                )
            )
            self._total_proposed += 1

            if not verdict.approved:
                # No human to ask, so the agent stands down on itself.
                self._journal.mark_blocked(entry.id)
                self._total_blocked += 1
                result.blocked += 1
                continue

            try:
                await self._alpaca.submit_order(proposal)
            except ValueError as e:
                result.errors.append(f"{proposal.symbol}: {e}")
                continue

            self._journal.mark_executed(
                entry.id, price=verdict.reference_price or signal.price
            )
            self._total_executed += 1
            result.executed += 1
            traded += 1

        self._last_cycle = result
        return result

    # ---------- status ----------

    @property
    def status(self) -> AgentStatus:
        return AgentStatus(
            enabled=self._enabled,
            loop_running=self.loop_running,
            market_open=self._last_cycle.market_open if self._last_cycle else None,
            interval_seconds=self._interval,
            universe=self._strategy.universe,
            cycles_completed=self._cycles,
            total_proposed=self._total_proposed,
            total_executed=self._total_executed,
            total_blocked=self._total_blocked,
            last_run_at=self._last_run_at,
            last_error=self._last_error,
            last_cycle=self._last_cycle,
        )
