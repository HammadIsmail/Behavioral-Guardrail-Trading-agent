from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.schemas.user import UserCreate, UserPublic, UserSettings
from app.services.user_service import UserService
from app.core.dependencies import get_user_service

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()

@router.post("/register", response_model=UserPublic)
async def register(
    user_create: UserCreate,
    user_service: UserService = Depends(get_user_service),
):
    existing = user_service.get_user_by_username(user_create.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    user = user_service.create_user(user_create)
    return UserPublic(id=user.id, username=user.username, settings=user.settings)

@router.post("/login")
async def login(
    username: str,
    password: str,
    user_service: UserService = Depends(get_user_service),
):
    user = user_service.get_user_by_username(username)
    if not user or not user_service.verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = user_service.create_access_token(user.id)
    return {"access_token": token, "token_type": "bearer"}

@router.get("/me", response_model=UserPublic)
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user_service: UserService = Depends(get_user_service),
):
    token = credentials.credentials
    user_id = user_service.decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = user_service.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return UserPublic(id=user.id, username=user.username, settings=user.settings)