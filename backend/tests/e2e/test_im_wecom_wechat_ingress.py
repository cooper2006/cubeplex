"""E2E tests for WeCom and WeChat webhook ingress routes."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubeplex.credentials.dependencies import build_credential_service
from cubeplex.im.wecom.binding_state import create_binding_state
from cubeplex.im.wecom.ingress import handle_inbound_callback
from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
)
from tests.e2e.conftest import _build_database_url
from tests.e2e.im_fixtures import im_cleanup, im_seed_account, im_seed_org_ws_user

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# WeCom constants
# ---------------------------------------------------------------------------

_WECOM_ORG_ID = "org-wecomA"
_WECOM_WS_ID = "ws-wecomA"
_WECOM_USER_ID = "usr-wecomA"
_WECOM_CORP_ID = "corp_wecomA"
_WECOM_TOKEN = "vt-wecom-ingress"
_WECOM_USERID = "u_wecom_user1"
_WECOM_MSG_ID = "msg_wecom_001"

# ---------------------------------------------------------------------------
# WeChat constants
# ---------------------------------------------------------------------------

_WECHAT_ORG_ID = "org-wchatA"
_WECHAT_WS_ID = "ws-wchatA"
_WECHAT_USER_ID = "usr-wchatA"
_WECHAT_APP_ID = "wx_appA"
_WECHAT_TOKEN = "vt-wchat-ingress"
_WECHAT_OPENID = "o_wchat_user1"
_WECHAT_MSG_ID = "msg_wchat_001"


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------


def _wecom_sign(token: str, timestamp: str, nonce: str, body: bytes) -> str:
    """Compute WeCom signature: SHA1(sorted(token, timestamp, nonce, body))."""
    sorted_params = sorted([token, timestamp, nonce])
    raw = "".join(sorted_params) + body.decode()
    return hashlib.sha1(raw.encode()).hexdigest()


def _wechat_sign(token: str, timestamp: str, nonce: str) -> str:
    """Compute WeChat signature: SHA1(sorted(token, timestamp, nonce))."""
    sorted_params = sorted([token, timestamp, nonce])
    raw = "".join(sorted_params)
    return hashlib.sha1(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# WeCom fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _seeded_wecom_account(
    async_client: httpx.AsyncClient,
) -> AsyncIterator[None]:
    """Seed a WeCom account with real encrypted credential."""
    transport = getattr(async_client, "_transport", None)
    asgi_transport: Any = transport
    if asgi_transport is None or not hasattr(asgi_transport, "app"):
        raise RuntimeError("async_client transport missing app")
    app = asgi_transport.app
    backend = app.state.encryption_backend

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await im_seed_org_ws_user(
                session, org_id=_WECOM_ORG_ID, ws_id=_WECOM_WS_ID, user_id=_WECOM_USER_ID
            )
            await session.commit()

        secret_payload = {
            "corp_id": _WECOM_CORP_ID,
            "token": _WECOM_TOKEN,
            "encoding_aes_key": "aes_key_32_chars_0000000000000000",
            "agent_id": 1000001,
        }
        async with maker() as session:
            svc = build_credential_service(
                session, backend, org_id=_WECOM_ORG_ID, actor_user_id=_WECOM_USER_ID
            )
            cred_id = await svc.create(
                kind="im_bot",
                name=f"wecom:{_WECOM_CORP_ID}",
                plaintext=json.dumps(secret_payload),
            )
            await session.commit()

        account_id = f"imac-wecomA-{cred_id[:8]}"
        async with maker() as session:
            await im_seed_account(
                session,
                account_id=account_id,
                org_id=_WECOM_ORG_ID,
                ws_id=_WECOM_WS_ID,
                user_id=_WECOM_USER_ID,
                credential_id=cred_id,
                external_account_id=_WECOM_CORP_ID,
                delivery_mode="webhook",
                platform="wecom",
            )
            await session.commit()

        try:
            yield None
        finally:
            async with maker() as session:
                await im_cleanup(
                    session,
                    account_ids=[account_id],
                    credential_ids=[cred_id],
                    ws_ids=[_WECOM_WS_ID],
                    cleanup_conversations_in_ws=True,
                )
                await session.commit()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def _seeded_wecom_pending_account(
    async_client: httpx.AsyncClient,
) -> AsyncIterator[tuple[str, str, async_sessionmaker[Any]]]:
    """Seed a PENDING WeCom account (disabled, gateway delivery mode).

    Yields (account_id, credential_id, session_maker).
    """
    transport = getattr(async_client, "_transport", None)
    asgi_transport: Any = transport
    if asgi_transport is None or not hasattr(asgi_transport, "app"):
        raise RuntimeError("async_client transport missing app")
    app = asgi_transport.app
    backend = app.state.encryption_backend

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await im_seed_org_ws_user(
                session, org_id=_WECOM_ORG_ID, ws_id=_WECOM_WS_ID, user_id=_WECOM_USER_ID
            )
            await session.commit()

        secret_payload = {
            "bot_id": _WECOM_CORP_ID,
            "secret": "wecom_bind_secret",
            "bot_open_id": _WECOM_CORP_ID,
        }
        async with maker() as session:
            svc = build_credential_service(
                session, backend, org_id=_WECOM_ORG_ID, actor_user_id=_WECOM_USER_ID
            )
            cred_id = await svc.create(
                kind="im_bot",
                name=f"wecom:pending_bindtest",
                plaintext=json.dumps(secret_payload),
            )
            await session.commit()

        account_id = "imac-wecomA-pending"
        async with maker() as session:
            await im_seed_account(
                session,
                account_id=account_id,
                org_id=_WECOM_ORG_ID,
                ws_id=_WECOM_WS_ID,
                user_id=_WECOM_USER_ID,
                credential_id=cred_id,
                external_account_id="pending_bindtest",
                delivery_mode="gateway",
                platform="wecom",
            )
            account = await session.get(IMConnectorAccount, account_id)
            account.enabled = False
            await session.commit()

        try:
            yield account_id, cred_id, maker
        finally:
            async with maker() as session:
                await im_cleanup(
                    session,
                    account_ids=[account_id],
                    credential_ids=[cred_id],
                    ws_ids=[_WECOM_WS_ID],
                    cleanup_conversations_in_ws=True,
                )
                await session.commit()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# WeChat fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _seeded_wechat_account(
    async_client: httpx.AsyncClient,
) -> AsyncIterator[None]:
    """Seed a WeChat Official Account with real encrypted credential."""
    transport = getattr(async_client, "_transport", None)
    asgi_transport: Any = transport
    if asgi_transport is None or not hasattr(asgi_transport, "app"):
        raise RuntimeError("async_client transport missing app")
    app = asgi_transport.app
    backend = app.state.encryption_backend

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await im_seed_org_ws_user(
                session, org_id=_WECHAT_ORG_ID, ws_id=_WECHAT_WS_ID, user_id=_WECHAT_USER_ID
            )
            await session.commit()

        secret_payload = {
            "appid": _WECHAT_APP_ID,
            "app_secret": "wx_secret",
            "token": _WECHAT_TOKEN,
            "encoding_aes_key": "wx_aes_32_chars_00000000000000",
        }
        async with maker() as session:
            svc = build_credential_service(
                session, backend, org_id=_WECHAT_ORG_ID, actor_user_id=_WECHAT_USER_ID
            )
            cred_id = await svc.create(
                kind="im_bot",
                name=f"wechat:{_WECHAT_APP_ID}",
                plaintext=json.dumps(secret_payload),
            )
            await session.commit()

        account_id = f"imac-wchatA-{cred_id[:8]}"
        async with maker() as session:
            await im_seed_account(
                session,
                account_id=account_id,
                org_id=_WECHAT_ORG_ID,
                ws_id=_WECHAT_WS_ID,
                user_id=_WECHAT_USER_ID,
                credential_id=cred_id,
                external_account_id=_WECHAT_APP_ID,
                delivery_mode="webhook",
                platform="wechat",
            )
            await session.commit()

        try:
            yield None
        finally:
            async with maker() as session:
                await im_cleanup(
                    session,
                    account_ids=[account_id],
                    credential_ids=[cred_id],
                    ws_ids=[_WECHAT_WS_ID],
                    cleanup_conversations_in_ws=True,
                )
                await session.commit()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# WeCom test cases
# ---------------------------------------------------------------------------


def _wecom_xml_body(*, msg_type: str = "text", content: str = "hello", from_user: str = "") -> bytes:
    """Build a WeCom XML webhook body."""
    from_user = from_user or _WECOM_USERID
    return (
        f"<xml>"
        f"<ToUserName><![CDATA[{_WECOM_CORP_ID}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>1700000000</CreateTime>"
        f"<MsgType><![CDATA[{msg_type}]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        f"<MsgId><![CDATA[{_WECOM_MSG_ID}]]></MsgId>"
        f"</xml>"
    ).encode()


@patch(
    "cubeplex.api.routes.v1.im_ingress.ingest_inbound_event",
    autospec=True,
)
async def test_wecom_text_message_enqueues_run(
    mock_ingest: Any,
    async_client: httpx.AsyncClient,
    _seeded_wecom_account: None,
) -> None:
    """Valid WeCom text message with correct signature → 200 and enqueue."""
    body = _wecom_xml_body()
    ts = "1700000000"
    nonce = "wecom_nonce_001"
    signature = _wecom_sign(_WECOM_TOKEN, ts, nonce, body)

    resp = await async_client.post(
        "/api/v1/im/wecom/events",
        content=body,
        headers={
            "signature": signature,
            "timestamp": ts,
            "nonce": nonce,
            "Content-Type": "application/xml",
        },
    )
    assert resp.status_code == 200, resp.text

    # Verify ingest was called
    assert mock_ingest.call_count == 1
    call_args = mock_ingest.call_args
    event = call_args[0][0]
    assert event.platform == "wecom"
    assert event.text == "hello"
    assert event.sender_ref == _WECOM_USERID


@patch(
    "cubeplex.api.routes.v1.im_ingress.ingest_inbound_event",
    autospec=True,
)
async def test_wecom_url_verification_challenge(
    mock_ingest: Any,
    async_client: httpx.AsyncClient,
    _seeded_wecom_account: None,
) -> None:
    """WeCom URL verification challenge returns echostr without signature."""
    echostr = "wecom_challenge_123"
    resp = await async_client.get(
        f"/api/v1/im/wecom/events?echostr={echostr}"
    )
    assert resp.status_code == 200
    assert resp.text == echostr
    # ingest should NOT be called for challenge
    mock_ingest.assert_not_called()


async def test_wecom_bad_signature_rejected(
    async_client: httpx.AsyncClient,
    _seeded_wecom_account: None,
) -> None:
    """WeCom request with invalid signature → 401."""
    body = _wecom_xml_body()
    headers = {
        "signature": "deadbeef00000000000000000000000000000000",
        "timestamp": "1700000000",
        "nonce": "wecom_nonce_bad",
        "Content-Type": "application/xml",
    }
    resp = await async_client.post(
        "/api/v1/im/wecom/events",
        content=body,
        headers=headers,
    )
    assert resp.status_code == 401


async def test_wecom_unknown_corp_ack_dropped(
    async_client: httpx.AsyncClient,
) -> None:
    """WeCom request for unknown corp → 404 (no enabled accounts)."""
    body = _wecom_xml_body()
    resp = await async_client.post(
        "/api/v1/im/wecom/events",
        content=body,
        headers={
            "signature": "any",
            "timestamp": "1700000000",
            "nonce": "any",
            "Content-Type": "application/xml",
        },
    )
    # No enabled WeCom accounts → 404
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# WeCom gateway /connect binding (pending account ingress)
# ---------------------------------------------------------------------------


def _wecom_ws_frame(*, user_id: str, content: str, msgid: str = "msg-bind-1") -> dict[str, Any]:
    """Build a WeCom WS callback frame as WecomGateway._inbound_handler receives it."""
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-bind-1"},
        "body": {
            "msgid": msgid,
            "chattype": "single",
            "from": {"userid": user_id},
            "msgtype": "text",
            "text": {"content": content},
        },
    }


class _StubWecomGateway:
    def __init__(self) -> None:
        self.replies: list[tuple[str, dict[str, Any]]] = []

    async def send_proactive(self, chat_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.replies.append((chat_id, body))
        return {"errcode": 0}

    def is_open(self) -> bool:
        return True


async def test_wecom_pending_account_connect_binds_and_enables(
    async_client: httpx.AsyncClient,
    _seeded_wecom_pending_account: tuple[str, str, async_sessionmaker[Any]],
) -> None:
    """A /connect message on a pending (disabled) account must bind it.

    Regression: the ingress guard dropped every callback for disabled
    accounts — which is exactly how pending accounts are stored until
    /connect <code> binds them, so the binding code could never work.
    """
    transport = getattr(async_client, "_transport", None)
    app = transport.app
    account_id, _cred_id, maker = _seeded_wecom_pending_account

    async with maker() as session:
        account = await session.get(IMConnectorAccount, account_id)
        assert account is not None
        assert account.enabled is False

    redis = app.state.redis
    key_prefix = getattr(app.state, "redis_key_prefix", "cubeplex")
    code = await create_binding_state(redis, key_prefix=key_prefix, account_id=account_id)

    gateway = _StubWecomGateway()
    raw = _wecom_ws_frame(user_id="wecom_bound_user", content=f"/connect {code}")
    await handle_inbound_callback(
        raw,
        account=account,
        session_maker=maker,
        gateway=gateway,
        redis=redis,
        redis_key_prefix=key_prefix,
    )

    async with maker() as session:
        bound = await session.get(IMConnectorAccount, account_id)
        assert bound is not None
        assert bound.enabled is True
        assert bound.external_account_id == "wecom_bound_user"
    assert any("绑定成功" in str(reply) for _, reply in gateway.replies)


async def test_wecom_disabled_non_pending_account_drops_connect_message(
    async_client: httpx.AsyncClient,
    _seeded_wecom_account: None,
) -> None:
    """Explicitly disabled (non-pending) accounts must NOT process /connect."""
    transport = getattr(async_client, "_transport", None)
    app = transport.app
    # Reuse the seeded enabled account, then disable it.
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            rows = (await session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.workspace_id == _WECOM_WS_ID,  # type: ignore[arg-type]
                    IMConnectorAccount.platform == "wecom",  # type: ignore[arg-type]
                )
            )).scalars().all()
            target = next(a for a in rows if not str(a.external_account_id).startswith("pending_"))
            target.enabled = False
            await session.commit()

        redis = app.state.redis
        key_prefix = getattr(app.state, "redis_key_prefix", "cubeplex")
        code = await create_binding_state(redis, key_prefix=key_prefix, account_id=target.id)

        gateway = _StubWecomGateway()
        raw = _wecom_ws_frame(user_id="wecom_stranger", content=f"/connect {code}")
        await handle_inbound_callback(
            raw,
            account=target,
            session_maker=maker,
            gateway=gateway,
            redis=redis,
            redis_key_prefix=key_prefix,
        )

        async with maker() as session:
            after = await session.get(IMConnectorAccount, target.id)
            assert after is not None
            assert after.enabled is False
            assert gateway.replies == []
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# WeChat test cases
# ---------------------------------------------------------------------------


def _wechat_xml_body(*, msg_type: str = "text", content: str = "hello", from_user: str = "") -> bytes:
    """Build a WeChat XML webhook body."""
    from_user = from_user or _WECHAT_OPENID
    return (
        f"<xml>"
        f"<ToUserName><![CDATA[{_WECHAT_APP_ID}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>1700000000</CreateTime>"
        f"<MsgType><![CDATA[{msg_type}]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        f"<MsgId><![CDATA[{_WECHAT_MSG_ID}]]></MsgId>"
        f"</xml>"
    ).encode()


@patch(
    "cubeplex.api.routes.v1.im_ingress.ingest_inbound_event",
    autospec=True,
)
async def test_wechat_text_message_enqueues_run(
    mock_ingest: Any,
    async_client: httpx.AsyncClient,
    _seeded_wechat_account: None,
) -> None:
    """Valid WeChat text message with correct signature → 200 and enqueue."""
    body = _wechat_xml_body()
    ts = "1700000000"
    nonce = "wechat_nonce_001"
    signature = _wechat_sign(_WECHAT_TOKEN, ts, nonce)

    resp = await async_client.post(
        "/api/v1/im/wechat/events",
        content=body,
        headers={
            "signature": signature,
            "timestamp": ts,
            "nonce": nonce,
            "Content-Type": "application/xml",
        },
    )
    assert resp.status_code == 200, resp.text

    assert mock_ingest.call_count == 1
    call_args = mock_ingest.call_args
    event = call_args[0][0]
    assert event.platform == "wechat"
    assert event.text == "hello"
    assert event.sender_ref == _WECHAT_OPENID


@patch(
    "cubeplex.api.routes.v1.im_ingress.ingest_inbound_event",
    autospec=True,
)
async def test_wechat_url_verification_challenge(
    mock_ingest: Any,
    async_client: httpx.AsyncClient,
    _seeded_wechat_account: None,
) -> None:
    """WeChat URL verification challenge returns echostr."""
    echostr = "wechat_challenge_456"
    resp = await async_client.get(
        f"/api/v1/im/wechat/events?echostr={echostr}"
    )
    assert resp.status_code == 200
    assert resp.text == echostr
    mock_ingest.assert_not_called()


async def test_wechat_bad_signature_rejected(
    async_client: httpx.AsyncClient,
    _seeded_wechat_account: None,
) -> None:
    """WeChat request with invalid signature → 401."""
    body = _wechat_xml_body()
    headers = {
        "signature": "deadbeef00000000000000000000000000000000",
        "timestamp": "1700000000",
        "nonce": "wechat_nonce_bad",
        "Content-Type": "application/xml",
    }
    resp = await async_client.post(
        "/api/v1/im/wechat/events",
        content=body,
        headers=headers,
    )
    assert resp.status_code == 401


async def test_wechat_unknown_app_ack_dropped(
    async_client: httpx.AsyncClient,
) -> None:
    """WeChat request for unknown app → 404 (no enabled accounts)."""
    body = _wechat_xml_body()
    resp = await async_client.post(
        "/api/v1/im/wechat/events",
        content=body,
        headers={
            "signature": "any",
            "timestamp": "1700000000",
            "nonce": "any",
            "Content-Type": "application/xml",
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cross-platform: ignore unsupported msg_types
# ---------------------------------------------------------------------------


@patch(
    "cubeplex.api.routes.v1.im_ingress.ingest_inbound_event",
    autospec=True,
)
async def test_wecom_ignore_event_type(
    mock_ingest: Any,
    async_client: httpx.AsyncClient,
    _seeded_wecom_account: None,
) -> None:
    """WeCom event with unsupported MsgType → 200 but no enqueue."""
    body = _wecom_xml_body(msg_type="event", content="subscribe")
    ts = "1700000000"
    nonce = "wecom_nonce_event"
    signature = _wecom_sign(_WECOM_TOKEN, ts, nonce, body)

    resp = await async_client.post(
        "/api/v1/im/wecom/events",
        content=body,
        headers={
            "signature": signature,
            "timestamp": ts,
            "nonce": nonce,
            "Content-Type": "application/xml",
        },
    )
    assert resp.status_code == 200
    mock_ingest.assert_not_called()


@patch(
    "cubeplex.api.routes.v1.im_ingress.ingest_inbound_event",
    autospec=True,
)
async def test_wechat_ignore_event_type(
    mock_ingest: Any,
    async_client: httpx.AsyncClient,
    _seeded_wechat_account: None,
) -> None:
    """WeChat event with unsupported MsgType → 200 but no enqueue."""
    body = _wechat_xml_body(msg_type="event", content="subscribe")
    ts = "1700000000"
    nonce = "wechat_nonce_event"
    signature = _wechat_sign(_WECHAT_TOKEN, ts, nonce)

    resp = await async_client.post(
        "/api/v1/im/wechat/events",
        content=body,
        headers={
            "signature": signature,
            "timestamp": ts,
            "nonce": nonce,
            "Content-Type": "application/xml",
        },
    )
    assert resp.status_code == 200
    mock_ingest.assert_not_called()
