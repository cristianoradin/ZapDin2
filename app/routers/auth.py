from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
import aiosqlite

from ..core.database import get_db
from ..core.security import verify_password, create_session_token, SESSION_COOKIE, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, response: Response, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT id, username, password_hash FROM usuarios WHERE username = ?", (body.username,)
    ) as cur:
        row = await cur.fetchone()

    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")

    token = create_session_token(row["id"], row["username"])
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return {"ok": True, "username": row["username"]}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"username": user["usr"], "uid": user["uid"]}
