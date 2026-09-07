"""WeChat outbound renderer — plain text message updates."""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from cubeplex.im.outbound import note_edit_success, note_flood_strike
from cubeplex.im.types import RenderState
from cubeplex.im.wechat.connector import WeChatConnector, WeChatRateLimitError


class WeChatOpDispatcher:
    """Dispatches outbound ops to WeChat via text messages."""

    def __init__(self, *, connector: WeChatConnector, state: RenderState) -> None:
        self._connector = connector
        self._state = state
        self._stream_seq: int = 0
        self._last_send_time: float = 0.0

    # ---- card lifecycle --------------------------------------------------

    async def dispatch_create(self, state: Any) -> bool:
        """No card creation for WeChat — just send initial message."""
        s = self._state
        s.card_id = f"cubeplex-{s.run_id}-{uuid.uuid4().hex[:8]}"
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        """Send streaming text update."""
        s = self._state
        # WeChat rate limit: ~1 msg/s for custom messages
        now = state.time.monotonic()
        if now - self._last_send_time < 1.0:
            return False
        self._last_send_time = now

        try:
            msg_id = await self._connector.send_to_chat(
                chat_id=self._connector._openid or state.chat_id,
                reply_to_id=None,  # WeChat doesn't support reply IDs
                text=text,
            )
            if msg_id:
                s.bot_message_id = msg_id
                return True
        except WeChatRateLimitError as e:
            note_flood_strike(self._state, e)
            return False
        except Exception:
            logger.exception("[WeChat] stream send failed")
            return False
        return False

    async def dispatch_finalize(self, state: Any) -> bool:
        """Send final message."""
        s = self._state
        final_text = getattr(getattr(s, "card_state", None), "streaming_content", "") or ""
        return await self.dispatch_stream(state, final_text)

    async def dispatch_error(self, state: Any, error: str) -> bool:
        """Send error message."""
        s = self._state
        return await self.dispatch_stream(state, f"❌ Error: {error}")

    # ---- reaction helpers ----------------------------------------------

    async def add_reaction(self, message_id: str, reaction_type: str) -> bool:
        """Not supported by WeChat API."""
        return False

    async def remove_reaction(self, message_id: str, reaction_id: str) -> bool:
        """Not supported by WeChat API."""
        return False

    def mark_edit_success(self, state: Any, msg_id: str) -> None:
        """Mark that an edit was successfully sent."""
        note_edit_success(self._state, msg_id)

    async def aclose(self) -> None:
        """Release resources. WeChat doesn't maintain persistent connections."""
        pass
