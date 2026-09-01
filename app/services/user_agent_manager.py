from typing import Dict, Optional
import asyncio
from app.core.config import Settings, get_settings
from app.services.alpaca_client import AlpacaClient
from app.services.guardrail_service import GuardrailService
from app.services.explainer import ExplainerService
from app.services.journal_service import JournalService
from app.services.strategy import MomentumStrategy, StrategyService
from app.services.agent import AgentService
from app.services.user_service import UserService
from app.schemas.user import UserSettings

class UserAgentManager:
    def __init__(self):
        self._agents: Dict[str, AgentService] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._stop_events: Dict[str, asyncio.Event] = {}
        self._settings = get_settings()
        self._user_service = UserService(
            db_url=self._settings.database_url,
            db_path=self._settings.journal_db_path
        )

    def get_or_create_agent(self, user_id: str) -> Optional[AgentService]:
        if user_id in self._agents:
            return self._agents[user_id]
        user = self._user_service.get_user_by_id(user_id)
        if not user:
            return None
        settings = user.settings
        # Create per-user services
        alpaca = AlpacaClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            base_url=self._settings.alpaca_base_url,
            data_url=self._settings.alpaca_data_url
        )
        guardrail = GuardrailService(alpaca)
        explainer = ExplainerService(self._settings)
        journal = JournalService(
            db_url=self._settings.database_url,
            db_path=self._settings.journal_db_path,
            user_id=user_id
        )
        strategy = MomentumStrategy(
            short_window=self._settings.strategy_short_window,
            long_window=self._settings.strategy_long_window,
            base_position_pct=self._settings.strategy_base_position_pct,
            max_conviction_multiple=self._settings.strategy_max_conviction_multiple,
            max_total_exposure_pct=self._settings.strategy_max_total_exposure_pct
        )
        strategy_service = StrategyService(alpaca, strategy, self._settings.universe)
        agent = AgentService(
            strategy=strategy_service,
            guardrail=guardrail,
            alpaca=alpaca,
            journal=journal,
            explainer=explainer,
            settings=self._settings  # but interval overridden from user settings? We'll pass a modified Settings or just override.
        )
        # Override interval and enabled from user settings
        agent._interval = max(30, settings.agent_interval_seconds)
        agent._enabled = settings.agent_enabled
        self._agents[user_id] = agent
        return agent

    def start_agent(self, user_id: str) -> bool:
        if user_id in self._tasks and not self._tasks[user_id].done():
            return False
        agent = self.get_or_create_agent(user_id)
        if not agent or not agent._enabled:
            return False
        stop_event = asyncio.Event()
        self._stop_events[user_id] = stop_event
        task = asyncio.create_task(self._run_agent_loop(agent, stop_event), name=f"agent-{user_id}")
        self._tasks[user_id] = task
        return True

    async def _run_agent_loop(self, agent: AgentService, stop_event: asyncio.Event):
        while not stop_event.is_set():
            try:
                await agent.run_cycle()
            except Exception as e:
                agent._last_error = f"{type(e).__name__}: {e}"
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=agent._interval)
            except asyncio.TimeoutError:
                continue

    async def stop_agent(self, user_id: str):
        if user_id in self._stop_events:
            self._stop_events[user_id].set()
        if user_id in self._tasks:
            task = self._tasks[user_id]
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
            del self._tasks[user_id]
        if user_id in self._agents:
            await self._agents[user_id].stop()
            # close alpaca client?
            await self._agents[user_id]._alpaca.aclose()
            del self._agents[user_id]
        if user_id in self._stop_events:
            del self._stop_events[user_id]

    async def stop_all(self):
        for user_id in list(self._tasks.keys()):
            await self.stop_agent(user_id)

    def update_settings(self, user_id: str, new_settings: UserSettings) -> bool:
        user = self._user_service.get_user_by_id(user_id)
        if not user:
            return False
        updated = self._user_service.update_settings(user_id, new_settings)
        if not updated:
            return False
        # If agent exists, update its interval and enabled, and restart if needed
        if user_id in self._agents:
            agent = self._agents[user_id]
            old_enabled = agent._enabled
            agent._interval = max(30, new_settings.agent_interval_seconds)
            agent._enabled = new_settings.agent_enabled
            # If enabled changed from false to true, start
            if not old_enabled and new_settings.agent_enabled:
                self.start_agent(user_id)
            elif old_enabled and not new_settings.agent_enabled:
                # stop
                asyncio.create_task(self.stop_agent(user_id))
        return True