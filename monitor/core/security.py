from typing import Optional

import bcrypt
from fastapi import Cookie, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

_serializer = URLSafeTimedSerializer(settings.secret_key)

SESSION_COOKIE = "zapdin_monitor_session"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_session_token(user_id: int, username: str, role: str = "admin") -> str:
    return _serializer.dumps({"uid": user_id, "usr": username, "role": role}, salt="monitor-session")


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
