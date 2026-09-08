"""Unit tests for the WeChat outbound renderer."""

from __future__ import annotations

from typing import Any

from cubeplex.im.types import RenderState
from cubeplex.im.wechat.renderer import WeChatOpDispatcher


class _RecordingConnector:
    """Stands in for WeChatConnector, recording the send call verbatim."""

    def __init__(self, *, chat_id: str = "o_user1") -> None:
        self._chat_id = chat_id
        self.calls: list[tuple[str, str | None, str]] = []

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> str:
        self.calls.append((chat_id, reply_to_id, text))
        return "msg-1"


def _state(**overrides: Any) -> RenderState:
    state = RenderState(bot_name="CubePlex", run_id="run-1", reply_to_id="ctx-1")
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


class TestWeChatDispatchStream:
    """The renderer must call send_to_chat with the protocol signature."""

    async def test_stream_sends_with_chat_id_and_reply_to_id(self) -> None:
        """Positional (chat_id, reply_to_id, text) — not keyword reply_to_id."""
        connector = _RecordingConnector()
        state = _state()
        dispatcher = WeChatOpDispatcher(connector=connector, state=state)

        assert await dispatcher.dispatch_stream(state, "hello") is True
        assert connector.calls == [("o_user1", "ctx-1", "hello")]
        assert state.bot_message_id == "msg-1"

    async def test_finalize_sends_streaming_content(self) -> None:
        """Finalize flushes whatever the card state accumulated."""
        connector = _RecordingConnector()
        state = _state()
        state.card_state.streaming_content = "final answer"
        dispatcher = WeChatOpDispatcher(connector=connector, state=state)

        assert await dispatcher.dispatch_finalize(state) is True
        assert connector.calls == [("o_user1", "ctx-1", "final answer")]
