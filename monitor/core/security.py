from typing import Optional

from fastapi import Cookie, HTTPException, status
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_serializer = URLSafeTimedSerializer(settings.secret_key)

SESSION_COOKIE = "zapdin_monitor_session"


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_session_token(user_id: int, username: str) -> str:
    return _serializer.dumps({"uid": user_id, "usr": username}, salt="monitor-session")


def decode_session_token(token: str) -> Optional[dict]:
    try:
        return _serializer.loads(token, salt="monitor-session", max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(
    zapdin_monitor_session: Optional[str] = Cookie(default=None),
) -> dict:
    if not zapdin_monitor_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autenticado")
    payload = decode_session_token(zapdin_monitor_session)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão expirada")
    return payload
