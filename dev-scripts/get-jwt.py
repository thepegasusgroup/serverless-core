#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Get a Supabase access token without waiting for an email.

Uses admin.generate_link (service_role) to produce a one-time code, then
calls /verify (anon) to exchange it for an access_token. Paste the output
into `sc login` during M1 development.

Usage:
    uv run dev-scripts/get-jwt.py

Reads SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY from .env
in the repo root.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx


def load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    load_env()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon = os.environ.get("SUPABASE_ANON_KEY")
    service = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    missing = [k for k, v in {
        "SUPABASE_URL": url,
        "SUPABASE_ANON_KEY": anon,
        "SUPABASE_SERVICE_ROLE_KEY": service,
    }.items() if not v]
    if missing:
        print(f"Missing in .env: {', '.join(missing)}", file=sys.stderr)
        return 1

    email = input("Email: ").strip()
    if not email:
        print("Email required", file=sys.stderr)
        return 1

    r = httpx.post(
        f"{url}/auth/v1/admin/generate_link",
        headers={
            "apikey": service,
            "Authorization": f"Bearer {service}",
            "Content-Type": "application/json",
        },
        json={"type": "magiclink", "email": email},
        timeout=30.0,
    )
    if r.status_code >= 400:
        print(f"generate_link failed: {r.status_code}\n{r.text}", file=sys.stderr)
        return 2

    payload = r.json()
    props = payload.get("properties") or payload
    otp = props.get("email_otp")
    if not otp:
        print(f"No email_otp in response:\n{payload}", file=sys.stderr)
        return 2

    v = httpx.post(
        f"{url}/auth/v1/verify",
        headers={"apikey": anon, "Content-Type": "application/json"},
        json={"email": email, "token": otp, "type": "email"},
        timeout=30.0,
    )
    if v.status_code >= 400:
        print(f"verify failed: {v.status_code}\n{v.text}", file=sys.stderr)
        return 3

    session = v.json()
    access_token = session.get("access_token")
    if not access_token:
        print(f"No access_token in response:\n{session}", file=sys.stderr)
        return 3

    api_url = os.environ.get("PUBLIC_API_URL", "http://localhost:8000").rstrip("/")
    config_dir = Path.home() / ".config" / "serverless-core"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        f'api_url = "{api_url}"\njwt = "{access_token}"\n',
        encoding="utf-8",
    )
    print(f"\n✓ Saved access token to {config_path}")
    print(f"  api_url = {api_url}")
    print(f"  (token len={len(access_token)}, starts with {access_token[:20]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
