"""
auth.py
=======
JWT Bearer Authentication and Client Security module for sez_server.

Features:
- Issues HMAC-SHA256 signed JWT tokens upon authenticating a valid pre-shared client API key.
- Validates JWT Bearer tokens on protected endpoints using FastAPI dependency injection.
- Employs constant-time string comparison (secrets.compare_digest) to prevent timing attacks.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, Any

import jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv

load_dotenv()

# Authentication configuration from environment
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "sez_default_fallback_jwt_secret_key_32bytes_long").strip()
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256").strip()
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
CLIENT_API_KEY = os.getenv("CLIENT_API_KEY", "sez_client_master_key_2026").strip()

# FastAPI HTTPBearer security scheme
security_bearer = HTTPBearer(auto_error=False)


def verify_client_api_key(provided_key: str) -> bool:
    """
    Validates the provided client API key using constant-time string comparison.
    """
    if not CLIENT_API_KEY or not provided_key:
        return False
    return secrets.compare_digest(provided_key.strip(), CLIENT_API_KEY)


def create_access_token(
    subject: str,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> Tuple[str, int]:
    """
    Generates a signed JWT access token.
    
    Returns:
        (token_str, expires_in_seconds)
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=JWT_EXPIRE_MINUTES)

    expires_in_seconds = int((expire - now).total_seconds())

    to_encode: Dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra_claims:
        to_encode.update(extra_claims)

    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt, expires_in_seconds


def get_current_client(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> Dict[str, Any]:
    """
    FastAPI dependency verifying incoming JWT Bearer tokens on protected endpoints.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a valid Bearer token in the Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT token has expired. Request a new token via /api/v1/auth/token.",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token", error_description="The token has expired"'},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid JWT token signature or malformed token.",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
