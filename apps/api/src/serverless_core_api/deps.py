import hashlib
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


API_KEY_PREFIX = "sc_live_"

# In-memory fixed-window rate limiter: {key_id: (window_start_ts, count)}
# Runs per-process → 2 Fly machines = 2x effective limit. Acceptable MVP;
# move to Redis/postgres if we need strict fleet-wide limits.
_RATE_STATE: dict[str, tuple[float, int]] = {}


def _check_rate_limit(key_id: str, per_min: int | None) -> None:
    if not per_min:
        return
    now = time.time()
    window_start, count = _RATE_STATE.get(key_id, (now, 0))
    if now - window_start >= 60.0:
        _RATE_STATE[key_id] = (now, 1)
        return
    if count >= per_min:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit exceeded ({per_min}/min)",
        )
    _RATE_STATE[key_id] = (window_start, count + 1)


def require_api_key(
    authorization: str | None = Header(default=None),
    sb: Client = Depends(get_service_client),
) -> str:
    """Require a valid `Authorization: Bearer sc_live_...` header.

    Also enforces the per-key `requests_per_minute` limit. Returns the
    api_keys.id so callers can log usage.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing API key")
    token = authorization.split(" ", 1)[1].strip()
    if not token.startswith(API_KEY_PREFIX):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key format")

    key_hash = hashlib.sha256(token.encode()).hexdigest()
    res = (
        sb.table("api_keys")
        .select("id,revoked_at,requests_per_minute")
        .eq("key_hash", key_hash)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown API key")
    row = res.data[0]
    if row.get("revoked_at"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key revoked")

    _check_rate_limit(row["id"], row.get("requests_per_minute"))

    try:
        sb.table("api_keys").update({"last_used_at": "now()"}).eq("id", row["id"]).execute()
    except Exception:  # noqa: BLE001
        pass

    return row["id"]


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
