from functools import lru_cache
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.services.agent import AgentService
from app.services.alpaca_client import AlpacaClient
from app.services.behavior_gap import BehaviorGapService
from app.services.chat import ChatService
from app.services.explainer import ExplainerService
from app.services.guardrail_service import GuardrailService
from app.services.journal_service import JournalService
from app.services.movers import MoversService
from app.services.strategy import MomentumStrategy, StrategyService
from app.services.user_service import UserService
from app.services.user_agent_manager import UserAgentManager
from app.schemas.user import UserInDB

security = HTTPBearer(auto_error=False)


@lru_cache
def get_user_service() -> UserService:
    settings = get_settings()
    return UserService(
        db_url=settings.database_url,
        db_path=settings.journal_db_path
    )


@lru_cache
def get_user_agent_manager() -> UserAgentManager:
    return UserAgentManager()


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    user_service: UserService = Depends(get_user_service),
) -> UserInDB:
    token = None
    if credentials:
        token = credentials.credentials
    else:
        token = request.cookies.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    user_id = user_service.decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = user_service.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    user_service: UserService = Depends(get_user_service),
) -> UserInDB | None:
    try:
        return get_current_user(request, credentials, user_service)
    except HTTPException:
        return None


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
    settings: Settings = get_settings()
    return JournalService(
        db_url=settings.database_url, db_path=settings.journal_db_path
    )


@lru_cache
def get_behavior_gap_service() -> BehaviorGapService:
    return BehaviorGapService(get_alpaca_client())


@lru_cache
def get_strategy_service() -> StrategyService:
    settings: Settings = get_settings()
    strategy = MomentumStrategy(
        short_window=settings.strategy_short_window,
        long_window=settings.strategy_long_window,
        base_position_pct=settings.strategy_base_position_pct,
        max_conviction_multiple=settings.strategy_max_conviction_multiple,
        max_total_exposure_pct=settings.strategy_max_total_exposure_pct,
    )
    return StrategyService(get_alpaca_client(), strategy, settings.universe)


@lru_cache
def get_agent_service() -> AgentService:
    return AgentService(
        strategy=get_strategy_service(),
        guardrail=get_guardrail_service(),
        alpaca=get_alpaca_client(),
        journal=get_journal_service(),
        explainer=get_explainer_service(),
        settings=get_settings(),
    )


@lru_cache
def get_movers_service() -> MoversService:
    settings: Settings = get_settings()
    return MoversService(get_alpaca_client(), settings.universe)


@lru_cache
def get_chat_service() -> ChatService:
    return ChatService(get_settings())


# Per-user dependencies
def get_alpaca_for_user(user: UserInDB = Depends(get_current_user)) -> AlpacaClient:
    settings = get_settings()
    return AlpacaClient(
        api_key=user.settings.alpaca_api_key,
        secret_key=user.settings.alpaca_secret_key,
        base_url=settings.alpaca_base_url,
        data_url=settings.alpaca_data_url
    )


def get_behavior_gap_for_user(alpaca: AlpacaClient = Depends(get_alpaca_for_user)) -> BehaviorGapService:
    return BehaviorGapService(alpaca)


def get_journal_for_user(user: UserInDB = Depends(get_current_user)) -> JournalService:
    settings = get_settings()
    return JournalService(
        db_url=settings.database_url,
        db_path=settings.journal_db_path,
        user_id=user.id
    )


def get_guardrail_for_user(alpaca: AlpacaClient = Depends(get_alpaca_for_user)) -> GuardrailService:
    return GuardrailService(alpaca)


def get_strategy_for_user(alpaca: AlpacaClient = Depends(get_alpaca_for_user)) -> StrategyService:
    settings = get_settings()
    strategy = MomentumStrategy(
        short_window=settings.strategy_short_window,
        long_window=settings.strategy_long_window,
        base_position_pct=settings.strategy_base_position_pct,
        max_conviction_multiple=settings.strategy_max_conviction_multiple,
        max_total_exposure_pct=settings.strategy_max_total_exposure_pct,
    )
    return StrategyService(alpaca, strategy, settings.universe)


def get_agent_for_user(
    user: UserInDB = Depends(get_current_user),
    manager: UserAgentManager = Depends(get_user_agent_manager),
) -> AgentService:
    agent = manager.get_or_create_agent(user.id)
    if not agent:
        raise HTTPException(status_code=400, detail="User settings incomplete")
    return agent


def get_alpaca_for_user_optional(
    user: UserInDB = Depends(get_current_user),
) -> AlpacaClient | None:
    """A per-user Alpaca client, or None when the user hasn't stored keys.

    None means "don't call Alpaca" — the UI renders a connect prompt instead of
    making authenticated calls with empty credentials.
    """
    if not (user.settings.alpaca_api_key and user.settings.alpaca_secret_key):
        return None
    settings = get_settings()
    return AlpacaClient(
        api_key=user.settings.alpaca_api_key,
        secret_key=user.settings.alpaca_secret_key,
        base_url=settings.alpaca_base_url,
        data_url=settings.alpaca_data_url,
    )
