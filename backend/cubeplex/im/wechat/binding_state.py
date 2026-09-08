"""WeChat QR code binding state — stored in Redis.

A user clicks "Connect" in the wizard → server creates a binding code →
returns a QR URL + code → user scans QR on their phone → sends /connect
<code> to the bot → gateway consumes the code and marks the account
connected.

State lives in Redis with a TTL so it expires automatically.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

logger = logging.getLogger(__name__)

# How long a binding code is valid (5 minutes).
_BINDING_CODE_TTL_SECONDS = 300

# Redis key pattern: {prefix}:wechat:binding:{code}
_KEY_PATTERN = "wechat:binding:{code}"


def generate_binding_code() -> str:
    """Generate a URL-safe random binding code."""
    return secrets.token_urlsafe(16)


async def create_binding_state(
    redis: Any,
    *,
    key_prefix: str,
    account_id: str,
    qrcode: str,
    qrcode_img_content: str | None = None,
    ttl_seconds: int = _BINDING_CODE_TTL_SECONDS,
) -> str:
    """Create a binding state entry and return the binding code.

    The binding code is stored alongside the QR data under a Redis key
    with a TTL. Returns the code so the caller can return it to the
    frontend.
    """
    code = generate_binding_code()
    key = f"{key_prefix}:{_KEY_PATTERN.format(code=code)}"
    payload: dict[str, Any] = {
        "code": code,
        "account_id": account_id,
        "qrcode": qrcode,
        "created_at": time.time(),
    }
    if qrcode_img_content:
        payload["qrcode_img_content"] = qrcode_img_content
    await redis.set(key, json.dumps(payload), ex=ttl_seconds)
    logger.info("[WeChat] created binding code=%s for account=%s", code, account_id)
    return code


async def consume_binding_state(
    redis: Any,
    *,
    key_prefix: str,
    code: str,
) -> dict[str, Any] | None:
    """Consume a binding code atomically. Returns payload or None if expired/invalid."""
    key = f"{key_prefix}:{_KEY_PATTERN.format(code=code)}"
    raw = await redis.get(key)
    if not raw:
        return None
    # Delete first to ensure atomicity
    deleted = await redis.delete(key)
    if not deleted:
        return None
    payload = json.loads(raw)
    logger.info("[WeChat] consumed binding code=%s for account=%s", code, payload.get("account_id"))
    return payload if isinstance(payload, dict) else None


async def delete_binding_state(
    redis: Any,
    *,
    key_prefix: str,
    code: str,
) -> None:
    """Invalidate a binding code so a stale one can never be redeemed."""
    key = f"{key_prefix}:{_KEY_PATTERN.format(code=code)}"
    await redis.delete(key)


async def get_pending_binding(
    redis: Any,
    *,
    key_prefix: str,
    code: str,
) -> dict[str, Any] | None:
    """Get binding state without consuming (for polling/retry)."""
    key = f"{key_prefix}:{_KEY_PATTERN.format(code=code)}"
    raw = await redis.get(key)
    if not raw:
        return None
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else None


# Redis key to track the current binding code for a pending account.
# Pattern: {prefix}:wechat:pending-account:{account_id} -> {code}
_PENDING_ACCOUNT_KEY = "wechat:pending-account:{account_id}"


async def store_pending_binding(
    redis: Any,
    *,
    key_prefix: str,
    account_id: str,
    code: str,
    ttl_seconds: int = _BINDING_CODE_TTL_SECONDS,
) -> None:
    """Store the current binding code for a pending account."""
    key = f"{key_prefix}:{_PENDING_ACCOUNT_KEY.format(account_id=account_id)}"
    await redis.set(key, code, ex=ttl_seconds)


async def get_pending_binding_for_account(
    redis: Any,
    *,
    key_prefix: str,
    account_id: str,
) -> str | None:
    """Get the current binding code for a pending account, if any."""
    key = f"{key_prefix}:{_PENDING_ACCOUNT_KEY.format(account_id=account_id)}"
    raw = await redis.get(key)
    return str(raw) if raw else None
