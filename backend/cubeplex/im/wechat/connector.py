"""WeChat connector: inbound parse + outbound message API (iLink protocol)."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from cubeplex.im.outbound import _FloodSignal
from cubeplex.im.types import (
    BindingMode,
    InboundEvent,
    make_participant_scope,
)


class WeChatRateLimitError(_FloodSignal):
    """Raised by WeChatConnector when iLink API responds with rate limit."""


class WeChatConnector:
    """Connector for one WeChat iLink bot account."""

    def __init__(
        self,
        *,
        chat_id: str = "",
        context_token: str | None = None,
    ) -> None:
        self._chat_id = chat_id
        self._context_token = context_token

    # ------------------------------------------------------------------
    # Lifecycle hooks (required by OutboundRunTailer)
    # ------------------------------------------------------------------

    async def on_processing_start(self, state: Any) -> None:
        """Called when run starts. WeChat doesn't support reactions."""
        pass

    async def on_processing_failed(self, state: Any) -> None:
        """Called when run fails. WeChat doesn't support reactions."""
        pass

    async def on_processing_complete(self, state: Any) -> None:
        """Called when run completes. WeChat doesn't support reactions."""
        pass

    @staticmethod
    def verify_signature(token: str, timestamp: str, nonce: str, signature: str) -> bool:
        """Verify WeChat message signature (legacy webhook mode)."""
        import hashlib
        import hmac
        sorted_params = sorted([token, timestamp, nonce])
        raw = "".join(sorted_params)
        computed = hashlib.sha1(raw.encode()).hexdigest()
        return hmac.compare_digest(computed, signature)

    def parse_ilink_message(
        self,
        raw: dict[str, Any],
        *,
        chat_id: str = "",
        binding_mode: BindingMode = "isolated",
    ) -> InboundEvent | None:
        """Parse iLink long-poll message → InboundEvent.

        iLink messages have this shape:
        {
            "message_type": 1,
            "from_user_id": "<openid>",
            "context_token": "<token>",
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            "client_id": "...",
            "msg_id": "...",
        }
        """
        msg_type = raw.get("message_type")
        if msg_type != 1:
            logger.debug("[WeChat] ignoring message_type=%s", msg_type)
            return None

        from_user = str(raw.get("from_user_id") or raw.get("ilink_user_id") or "").strip()
        if not from_user:
            return None

        text = self._extract_text(raw)

        context_token = str(raw.get("context_token") or "").strip()
        msg_id = str(raw.get("msg_id") or raw.get("client_id") or "").strip()

        # Truncate to 128 chars to match database VARCHAR(128) limits
        MAX_FIELD_LEN = 128
        from_user = from_user[:MAX_FIELD_LEN] if len(from_user) > MAX_FIELD_LEN else from_user
        context_token = context_token[:MAX_FIELD_LEN] if len(context_token) > MAX_FIELD_LEN else context_token
        msg_id = msg_id[:MAX_FIELD_LEN] if len(msg_id) > MAX_FIELD_LEN else msg_id

        scope_key = make_participant_scope(from_user)

        return InboundEvent(
            platform="wechat",
            account_external_id="",
            platform_event_id=msg_id,
            channel_id=chat_id or from_user,
            scope_key=scope_key,
            scope_kind="participant",
            reply_to_id=(context_token[:MAX_FIELD_LEN] if context_token else None),
            inbound_message_id=msg_id,
            sender_ref=from_user,
            sender_open_id=from_user,
            text=text,
        )

    @staticmethod
    def _extract_text(raw: dict[str, Any]) -> str:
        """Extract text from iLink message item_list."""
        item_list = raw.get("item_list") or []
        for item in item_list:
            if not isinstance(item, dict):
                continue
            if item.get("type") == 1:  # TEXT
                text_item = item.get("text_item") or {}
                return str(text_item.get("text") or "").strip()
        return ""
