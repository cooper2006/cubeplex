"""Unit tests for WeChat connector (signature verification + XML parsing)."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

import pytest


class TestWeChatSignature:
    """WeChat signature verification tests."""

    def test_verify_signature_valid(self) -> None:
        """Valid signature passes verification.

        WeChat signature = SHA1(sorted(token, timestamp, nonce)) — no body.
        """
        from cubeplex.im.wechat.connector import WeChatConnector

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
        from cubeplex.im.wechat.connector import WeChatConnector

        token = "test_token"
        timestamp = "1700000000"
        nonce = "test_nonce"
        computed = WeChatConnector.verify_signature(token, timestamp, nonce, "")
        wrong_sig = hashlib.sha1(b"wrong").hexdigest()

        assert computed is False


class TestWeChatXMLParsing:
    """WeChat XML parsing tests."""

    def test_parse_text_message(self) -> None:
        """Parse a standard WeChat text message."""
        from cubeplex.im.wechat.connector import WeChatConnector

        xml = (
            b'<xml>'
            b'<ToUserName><![CDATA[wx_app]]></ToUserName>'
            b'<FromUserName><![CDATA[o_user1]]></FromUserName>'
            b'<CreateTime>1700000000</CreateTime>'
            b'<MsgType><![CDATA[text]]></MsgType>'
            b'<Content><![CDATA[hello]]></Content>'
            b'<MsgId><![CDATA[msg001]]></MsgId>'
            b'</xml>'
        )

        connector = WeChatConnector()
        event = connector.parse_inbound(xml)

        assert event is not None
        assert event.platform == "wechat"
        assert event.text == "hello"
        assert event.sender_ref == "o_user1"
        assert event.platform_event_id == "msg001"

    def test_parse_invalid_xml(self) -> None:
        """Invalid XML returns None."""
        from cubeplex.im.wechat.connector import WeChatConnector

        connector = WeChatConnector()
        event = connector.parse_inbound(b"not xml")
        assert event is None

    def test_parse_missing_required_fields(self) -> None:
        """Message without FromUserName or MsgId returns None."""
        from cubeplex.im.wechat.connector import WeChatConnector

        xml = b'<xml><MsgType><![CDATA[text]]></MsgType></xml>'
        connector = WeChatConnector()
        event = connector.parse_inbound(xml)
        assert event is None

    def test_ignore_unsupported_msg_type(self) -> None:
        """Event messages are ignored."""
        from cubeplex.im.wechat.connector import WeChatConnector

        xml = (
            b'<xml>'
            b'<FromUserName><![CDATA[o_user1]]></FromUserName>'
            b'<MsgType><![CDATA[event]]></MsgType>'
            b'<Event><![CDATA[subscribe]]></Event>'
            b'</xml>'
        )
        connector = WeChatConnector()
        event = connector.parse_inbound(xml)
        assert event is None
