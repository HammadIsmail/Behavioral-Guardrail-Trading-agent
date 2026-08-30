from functools import lru_cache


from app.core.config import Settings, get_settings
from app.services.alpaca_client import AlpacaClient
from app.services.behavior_gap import BehaviorGapService
from app.services.explainer import ExplainerService
from app.services.guardrail_service import GuardrailService
from app.services.journal_service import JournalService


@lru_cache
def get_alpaca_client() -> AlpacaClient:
    settings: Settings = get_settings()
    return AlpacaClient(settings)


@lru_cache
def get_guardrail_service() -> GuardrailService:
    return GuardrailService(get_alpaca_client())


@lru_cache
def get_explainer_service() -> ExplainerService:
    settings: Settings = get_settings()
    return ExplainerService(settings)


@lru_cache
def get_journal_service() -> JournalService:
    return JournalService()


@lru_cache
def get_behavior_gap_service() -> BehaviorGapService:
    return BehaviorGapService(get_alpaca_client())
