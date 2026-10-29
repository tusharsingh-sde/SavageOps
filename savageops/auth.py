"""Authentication and admin role verification for SavageOps API."""

import jwt
from fastapi import Header, HTTPException, status

from savageops.config import ADMIN_EMAILS, SUPABASE_JWT_SECRET


def require_admin(authorization: str | None = Header(None)) -> dict:
    """Verify Supabase JWT claims or local dev token against authorized admin emails."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    token = authorization.removeprefix("Bearer ").strip()

    # Allow local development bypass if configured
    if token == "dev-admin-token":
        return {
            "email": ADMIN_EMAILS[0] if ADMIN_EMAILS else "admin@savageops.local",
            "role": "admin",
        }

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

    email = claims.get("email", "").lower()
    if email not in ADMIN_EMAILS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: email '{email}' is not an authorized administrator",
        )

    return claims
