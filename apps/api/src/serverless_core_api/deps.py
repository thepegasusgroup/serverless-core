import logging
import time
from functools import lru_cache

import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, status
from jwt import InvalidTokenError, PyJWKClient
from supabase import Client, create_client

from serverless_core_api.config import Settings, get_settings

logger = logging.getLogger("serverless_core_api.deps")


@lru_cache
def _build_service_client(url: str, key: str) -> Client:
    return create_client(url, key)


def get_service_client(settings: Settings = Depends(get_settings)) -> Client:
    return _build_service_client(settings.supabase_url, settings.supabase_service_role_key)


@lru_cache
def _jwks_client(supabase_url: str) -> PyJWKClient:
    url = f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    return PyJWKClient(url, cache_keys=True, lifespan=300)


_allowlist_cache: dict[str, tuple[bool, float]] = {}
_ALLOWLIST_TTL_SECONDS = 60.0


def _is_staff(email: str, sb: Client) -> bool:
    now = time.time()
    cached = _allowlist_cache.get(email)
    if cached and cached[1] > now:
        return cached[0]
    res = (
        sb.table("staff_allowlist")
        .select("email")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    ok = bool(res.data)
    _allowlist_cache[email] = (ok, now + _ALLOWLIST_TTL_SECONDS)
    return ok


def verify_agent_secret(
    x_agent_secret: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not x_agent_secret or x_agent_secret != settings.agent_shared_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid agent secret")


def _decode_supabase_jwt(token: str, settings: Settings) -> dict:
    try:
        signing_key = _jwks_client(settings.supabase_url).get_signing_key_from_jwt(token)
        claims = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
        logger.info("JWT verified via JWKS (alg=%s)", signing_key.key_type)
        return claims
    except InvalidTokenError as e:
        logger.warning("JWKS decode failed: %s", e)
        raise
    except Exception as e:
        logger.warning("JWKS fetch/verify errored: %s — falling back to HS256", e)

    claims = pyjwt.decode(
        token,
        settings.supabase_jwt_secret,
        algorithms=["HS256"],
        audience="authenticated",
    )
    logger.info("JWT verified via legacy HS256")
    return claims


def get_staff_user(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    sb: Client = Depends(get_service_client),
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    try:
        unverified_header = pyjwt.get_unverified_header(token)
        unverified_claims = pyjwt.decode(token, options={"verify_signature": False})
        logger.info(
            "Incoming JWT — len=%d, header=%s, aud=%s, email=%s",
            len(token), unverified_header,
            unverified_claims.get("aud"), unverified_claims.get("email"),
        )
    except Exception as e:
        logger.warning("Pre-check: couldn't parse token as JWT at all: %s (len=%d, head=%r)",
                       e, len(token), token[:30])

    try:
        claims = _decode_supabase_jwt(token, settings)
    except InvalidTokenError as e:
        logger.warning("All decode paths failed: %s", e)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {e}") from e

    email = claims.get("email")
    if not email:
        logger.warning("Token has no email claim: %s", claims)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing email claim")

    if not _is_staff(email, sb):
        logger.warning("Email %s not in staff_allowlist", email)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Email not in staff allowlist")

    logger.info("Staff authenticated: %s", email)
    return {"email": email, "claims": claims}
