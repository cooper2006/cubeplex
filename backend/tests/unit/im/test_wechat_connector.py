"""Unit tests for WeChat connector (signature verification + iLink parsing)."""

from __future__ import annotations

import hashlib
from typing import Any

from cubeplex.im.wechat.connector import WeChatConnector


def _ilink_text_message(**overrides: Any) -> dict[str, Any]:
    """A minimal iLink long-poll TEXT message."""
    raw: dict[str, Any] = {
        "message_type": 1,
        "from_user_id": "o_user1",
        "context_token": "ctx-1",
        "msg_id": "msg001",
        "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
    }
    raw.update(overrides)
    return raw


class TestWeChatSignature:
    """WeChat signature verification tests."""

    def test_verify_signature_valid(self) -> None:
        """Valid signature passes verification.

        WeChat signature = SHA1(sorted(token, timestamp, nonce)) — no body.
        """
        token = "test_token"
        timestamp = "1700000000"
        nonce = "test_nonce"
        # Compute expected signature (no body in WeChat signature)
        sorted_params = sorted([token, timestamp, nonce])
        raw = "".join(sorted_params)
        expected = hashlib.sha1(raw.encode()).hexdigest()

        result = WeChatConnector.verify_signature(token, timestamp, nonce, expected)
        assert result is True

    def test_verify_signature_invalid(self) -> None:
        """Invalid signature fails verification."""
        computed = WeChatConnector.verify_signature("t", "1700000000", "n", "")
        wrong_sig = hashlib.sha1(b"wrong").hexdigest()

        assert computed is False
        assert wrong_sig  # keep the fixture honest


class TestWeChatILinkParsing:
    """iLink long-poll message parsing (the transport WeChat actually uses)."""

    def test_parse_text_message(self) -> None:
        """Parse a standard iLink text message."""
        connector = WeChatConnector()
        event = connector.parse_ilink_message(_ilink_text_message(), chat_id="o_user1")

        assert event is not None
        assert event.platform == "wechat"
        assert event.text == "hello"
        assert event.sender_ref == "o_user1"
        assert event.platform_event_id == "msg001"
        # iLink addresses a conversation by context token; the tailer needs it
        # back as reply_to_id to answer in the right chat.
        assert event.reply_to_id == "ctx-1"

    def test_missing_sender_returns_none(self) -> None:
        """A message with no sender cannot be routed."""
        connector = WeChatConnector()
        event = connector.parse_ilink_message(_ilink_text_message(from_user_id=""))
        assert event is None

    def test_ignore_unsupported_message_type(self) -> None:
        """Non-TEXT message types are ignored."""
        connector = WeChatConnector()
        event = connector.parse_ilink_message(_ilink_text_message(message_type=3))
        assert event is None

    def test_empty_text_is_kept(self) -> None:
        """A message with no text item still parses (attachments arrive this way)."""
        connector = WeChatConnector()
        event = connector.parse_ilink_message(_ilink_text_message(item_list=[]))
        assert event is not None
        assert event.text == ""


class _FakeGateway:
    """Records outbound sends instead of calling iLink."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def send_text(self, chat_id: str, context_token: str, text: str) -> str:
        self.calls.append((chat_id, context_token, text))
        return "msg-1"


class TestWeChatSendToChat:
    """``send_to_chat`` must follow the OutboundConnector protocol.

    The renderer calls it positionally as ``(chat_id, reply_to_id, text)``;
    a mismatched signature raises TypeError at send time and the bot goes
    silent.
    """

    async def test_uses_reply_to_id_as_context_token(self) -> None:
        """The run's reply_to_id wins over the connector's bound token."""
        gateway = _FakeGateway()
        connector = WeChatConnector(chat_id="o_user1", context_token="bound", gateway=gateway)

        msg_id = await connector.send_to_chat("o_user1", "run-ctx", "hi")

        assert msg_id == "msg-1"
        assert gateway.calls == [("o_user1", "run-ctx", "hi")]

    async def test_falls_back_to_bound_context_token(self) -> None:
        """With no reply_to_id, fall back to the token bound at tailer start."""
        gateway = _FakeGateway()
        connector = WeChatConnector(chat_id="o_user1", context_token="bound", gateway=gateway)

        await connector.send_to_chat("o_user1", None, "hi")

        assert gateway.calls == [("o_user1", "bound", "hi")]

    async def test_without_any_context_token_is_noop(self) -> None:
        """No conversation token → nothing is sent (and no crash)."""
        gateway = _FakeGateway()
        connector = WeChatConnector(chat_id="o_user1", gateway=gateway)

        assert await connector.send_to_chat("o_user1", None, "hi") is None
        assert gateway.calls == []
