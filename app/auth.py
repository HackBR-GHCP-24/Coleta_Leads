from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config

_ITERATIONS = 310_000
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(candidate, digest)


def create_token(username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": now, "exp": now + timedelta(minutes=config.JWT_EXPIRES_MINUTES)}
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Não autenticado")
    try:
        payload = jwt.decode(creds.credentials, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão inválida ou expirada")
    return payload["sub"]
