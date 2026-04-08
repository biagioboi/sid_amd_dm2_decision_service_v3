from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext


# Use PBKDF2 so the reference project runs without native bcrypt wheels.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

OAUTH2_SCHEME = OAuth2PasswordBearer(tokenUrl="/auth/token")


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


JWT_SECRET = _env("JWT_SECRET", "dev-jwt-secret")
JWT_ALG = _env("JWT_ALG", "HS256")
JWT_EXPIRE_MIN = int(_env("JWT_EXPIRE_MINUTES", "60"))

DEV_USERNAME = _env("DEV_USERNAME", "admin")
DEV_PASSWORD = _env("DEV_PASSWORD", "admin")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# Minimal in-memory user store for reference implementation.
# Replace with DB-backed users in production.
_DEV_USERS = {
    DEV_USERNAME: {
        "username": DEV_USERNAME,
        "hashed_password": hash_password(DEV_PASSWORD),
        "roles": ["clinician"],
    }
}


def authenticate_user(username: str, password: str) -> Optional[dict]:
    user = _DEV_USERS.get(username)
    if not user:
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    return user


def create_access_token(*, sub: str, roles: list[str]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "roles": roles,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRE_MIN)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def get_current_user(token: str = Depends(OAUTH2_SCHEME)) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        username: str = payload.get("sub")
        if not username:
            raise credentials_exception
        user = _DEV_USERS.get(username)
        if not user:
            raise credentials_exception
        return {"username": username, "roles": payload.get("roles", [])}
    except JWTError:
        raise credentials_exception


def require_role(required: str):
    def _dep(user: dict = Depends(get_current_user)) -> dict:
        if required not in user.get("roles", []):
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return _dep
