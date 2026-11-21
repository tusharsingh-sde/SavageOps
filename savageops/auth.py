"""Authentication and admin role verification for SavageOps API."""

import hmac

import jwt
from fastapi import Header, HTTPException, status

from savageops.config import ADMIN_USER_IDS, SUPABASE_JWT_SECRET


def require_admin(authorization: str | None = Header(None)) -> dict:
    """Verify Supabase JWT claims against authorized admin user IDs."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format (expected 'Bearer <token>')",
        )
    token = parts[1].strip()

    if not SUPABASE_JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_JWT_SECRET is not configured",
        )

    try:
        claims = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )

    user_id = claims.get("sub", "")
    if not ADMIN_USER_IDS or not any(hmac.compare_digest(user_id, allowed) for allowed in ADMIN_USER_IDS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: user is not an authorized administrator",
        )

    return claims
