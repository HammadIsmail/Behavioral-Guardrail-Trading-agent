from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.services.user_service import UserService
from app.core.dependencies import get_user_service
from app.schemas.user import UserCreate

router = APIRouter(tags=["auth-ui"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    user_service: UserService = Depends(get_user_service),
):
    user = user_service.get_user_by_username(username)
    if not user or not user_service.verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = user_service.create_access_token(user.id)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(key="token", value=token, httponly=True, max_age=604800)
    return response

@router.post("/register")
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    user_service: UserService = Depends(get_user_service),
):
    existing = user_service.get_user_by_username(username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    user_create = UserCreate(username=username, password=password)
    user = user_service.create_user(user_create)
    # Log them in after registration
    token = user_service.create_access_token(user.id)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(key="token", value=token, httponly=True, max_age=604800)
    return response

@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("token")
    return response