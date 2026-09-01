from fastapi import APIRouter, Depends, HTTPException
from app.core.dependencies import get_current_user, get_user_service, get_user_agent_manager, get_agent_for_user
from app.schemas.user import UserPublic, UserSettings, UserInDB
from app.services.user_service import UserService
from app.services.user_agent_manager import UserAgentManager
from app.services.agent import AgentService
from app.schemas.strategy import AgentStatus, AgentCycleResult

router = APIRouter(prefix="/user", tags=["user"])

@router.get("/me", response_model=UserPublic)
async def get_me(user: UserInDB = Depends(get_current_user)):
    return UserPublic(id=user.id, username=user.username, settings=user.settings)

@router.put("/settings", response_model=UserPublic)
async def update_settings(
    settings: UserSettings,
    user: UserInDB = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
    manager: UserAgentManager = Depends(get_user_agent_manager),
):
    updated = manager.update_settings(user.id, settings)
    if not updated:
        raise HTTPException(status_code=400, detail="Failed to update settings")
    # Reload user
    user = user_service.get_user_by_id(user.id)
    return UserPublic(id=user.id, username=user.username, settings=user.settings)

@router.post("/agent/start")
async def start_agent(
    manager: UserAgentManager = Depends(get_user_agent_manager),
    user: UserInDB = Depends(get_current_user),
):
    started = manager.start_agent(user.id)
    return {"started": started}

@router.post("/agent/stop")
async def stop_agent(
    manager: UserAgentManager = Depends(get_user_agent_manager),
    user: UserInDB = Depends(get_current_user),
):
    await manager.stop_agent(user.id)
    return {"stopped": True}

@router.get("/agent/status", response_model=AgentStatus)
async def get_agent_status(
    agent: AgentService = Depends(get_agent_for_user),
):
    return agent.status

@router.post("/agent/run-once", response_model=AgentCycleResult)
async def run_agent_cycle(
    agent: AgentService = Depends(get_agent_for_user),
):
    return await agent.run_cycle()