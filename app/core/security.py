from typing import Optional

import bcrypt
from fastapi import Cookie, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

_serializer = URLSafeTimedSerializer(settings.secret_key)

SESSION_COOKIE = "zapdin_session"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_session_token(user_id: int, username: str) -> str:
    return _serializer.dumps({"uid": user_id, "usr": username}, salt="session")


def decode_session_token(token: str) -> Optional[dict]:
    try:
        return _serializer.loads(token, salt="session", max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(
    zapdin_session: Optional[str] = Cookie(default=None),
) -> dict:
    if not zapdin_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autenticado")
    payload = decode_session_token(zapdin_session)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão expirada")
    return payload


def verify_erp_token(token: str, stored_token: str) -> bool:
    return token == stored_token
