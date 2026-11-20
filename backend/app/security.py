"""Password hashing and signed bearer tokens on the stdlib (no extra deps)."""

import base64
import hashlib
import hmac
import json
import os
import time

from .config import get_settings

_ITER = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _ITER)
    return hmac.compare_digest(dk.hex(), dk_hex)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: str) -> str:
    key = get_settings().secret_key.encode()
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def make_token(subject: str, kind: str, ttl_hours: int | None = None) -> str:
    ttl = ttl_hours if ttl_hours is not None else get_settings().token_ttl_hours
    body = _b64(json.dumps({"sub": subject, "k": kind, "exp": int(time.time()) + ttl * 3600}).encode())
    return f"{body}.{_sign(body)}"


def read_token(token: str, kind: str) -> str | None:
    """Return subject if the token is valid, of the expected kind and not expired."""
    try:
        body, sig = token.split(".")
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        data = json.loads(_unb64(body))
    except ValueError:
        return None
    if data.get("k") != kind or data.get("exp", 0) < time.time():
        return None
    return str(data["sub"])
