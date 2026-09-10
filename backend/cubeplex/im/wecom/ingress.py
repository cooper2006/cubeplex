"""Account-bound WeCom callback routing."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.im.identity import NullIdentityResolver
from cubeplex.im.inbound import ingest_command_response, ingest_inbound_event
from cubeplex.im.reset_command import format_reset_reply
from cubeplex.im.types import lookup_binding_mode
from cubeplex.im.wecom.commands import parse_command, render_response
from cubeplex.im.wecom.connector import WecomConnector
from cubeplex.models.im_connector import IMConnectorAccount
from cubeplex.repositories.im_connector import (
    claim_command_response,
    mark_command_response_delivered,
    release_command_response_claim,
)

# A passive final can spend one ACK window draining an intermediate response,
# then proactive fallback spends another. Keep the ownership fence well beyond
# both windows plus database/network overhead.
_COMMAND_LEASE_SECONDS = 60

# Regex to extract /connect <code> from message text
_CONNECT_CODE_PATTERN = re.compile(r"^/connect\s+(\S+)", re.IGNORECASE)


def _extract_connect_code(text: str) -> str | None:
    """Extract binding code from /connect <code> message."""
    if not text:
        return None
    match = _CONNECT_CODE_PATTERN.match(text.strip())
    return match.group(1) if match else None


async def _handle_connect_code(
    *,
    event: Any,
    raw: dict[str, Any],
    account: IMConnectorAccount,
    session_maker: async_sessionmaker[AsyncSession],
    gateway: Any,
    redis: Any,
    redis_key_prefix: str,
) -> bool:
    """Handle /connect <code> binding for pending WeCom accounts.

    Returns True if the message was handled (binding completed or failed),
    False to continue with normal processing.
    """
    from cubeplex.im.wecom.binding_state import consume_binding_state

    code = _extract_connect_code(event.text)
    if not code or not redis:
        return False

    # Look up the binding state
    payload = await consume_binding_state(
        redis, key_prefix=redis_key_prefix, code=code
    )
    if payload is None:
        # Invalid or expired code - send reply
        await gateway.send_proactive(
            event.channel_id,
            {"msgtype": "markdown", "markdown": {"content": "连接码无效或已过期，请重新获取。"}},
        )
        logger.info("[WeCom] invalid/expired binding code={}", code)
        return True

    target_account_id = payload.get("account_id")
    if target_account_id != account.id:
        # Code belongs to a different account
        await gateway.send_proactive(
            event.channel_id,
            {"msgtype": "markdown", "markdown": {"content": "该连接码不属于此账号，请使用正确的连接码。"}},
        )
        logger.info("[WeCom] binding code={} mismatch for account={}", code, account.id)
        return True

    # Valid binding - activate the account
    async with session_maker() as session:
        live_account = await session.get(IMConnectorAccount, account.id)
        if live_account is None:
            return True
        live_account.external_account_id = str(
            raw.get("body", {}).get("from", {}).get("userid", "") or account.external_account_id
        )
        live_account.enabled = True
        await session.commit()

    logger.info("[WeCom] binding code={} succeeded for account={}", code, account.id)

    # Send success reply
    await gateway.send_proactive(
        event.channel_id,
        {"msgtype": "markdown", "markdown": {"content": "企业微信绑定成功！现在可以使用该机器人了。"}},
    )
    return True


def _delivery_error_code(response: dict[str, Any]) -> int:
    source = response
    if "errcode" not in source:
        body = response.get("body")
        source = body if isinstance(body, dict) else {}
    try:
        return int(source.get("errcode") or 0)
    except (TypeError, ValueError):
        return -1


async def _deliver_command_response(
    *,
    receipt_id: str,
    req_id: str,
    chat_id: str,
    session_maker: async_sessionmaker[AsyncSession],
    gateway: Any,
) -> None:
    async with session_maker() as session:
        claim = await claim_command_response(
            session,
            receipt_id=receipt_id,
            lease_seconds=_COMMAND_LEASE_SECONDS,
        )
        await session.commit()
    if claim is None:
        return

    text = render_response(claim.payload)
    body = {"msgtype": "markdown", "markdown": {"content": text}}
    try:
        result = await gateway.send_passive(
            req_id,
            body,
            final=True,
            skip_if_pending=False,
        )
        if result.get("proactive_required") or _delivery_error_code(result):
            proactive_result = await gateway.send_proactive(chat_id, body)
            if _delivery_error_code(proactive_result):
                raise RuntimeError("WeCom rejected the proactive command response")
    except Exception:
        async with session_maker() as session:
            await release_command_response_claim(
                session,
                receipt_id=receipt_id,
                lease_expires_at=claim.lease_expires_at,
            )
            await session.commit()
        logger.opt(exception=True).warning(
            "[WeCom] command response delivery failed for receipt {}",
            receipt_id,
        )
        return

    async with session_maker() as session:
        await mark_command_response_delivered(
            session,
            receipt_id=receipt_id,
            lease_expires_at=claim.lease_expires_at,
        )
        await session.commit()


async def handle_inbound_callback(
    raw: dict[str, Any],
    *,
    account: IMConnectorAccount,
    session_maker: async_sessionmaker[AsyncSession],
    gateway: Any,
    redis: Any = None,
    redis_key_prefix: str = "cubeplex",
) -> None:
    """Route one WeCom callback through commands or ordinary ingestion."""
    async with session_maker() as session:
        live_account = await session.get(IMConnectorAccount, account.id)
        if live_account is None:
            return
        # Pending accounts are created disabled and stay that way until a
        # /connect code binds them; dropping them here would make the
        # binding message unreachable. Explicitly disabled accounts are
        # still dropped.
        if not live_account.enabled and not str(
            live_account.external_account_id
        ).startswith("pending_"):
            return
        account = live_account

    body = raw.get("body")
    body = body if isinstance(body, dict) else {}
    sender = body.get("from")
    sender = sender if isinstance(sender, dict) else {}
    channel_id = str(body.get("chatid") or sender.get("userid") or "").strip()
    binding_mode = await lookup_binding_mode(session_maker, account.id, channel_id)
    connector = WecomConnector(
        bot_id=account.external_account_id,
        bot_display_name=str((account.config or {}).get("bot_app_name") or ""),
        gateway=gateway,
    )
    event = connector.parse_inbound(raw, binding_mode=binding_mode)
    if event is None:
        return
    event.account_external_id = account.external_account_id

    # Handle /connect <code> binding
    connect_code = _extract_connect_code(event.text)
    if connect_code:
        handled = await _handle_connect_code(
            event=event,
            raw=raw,
            account=account,
            session_maker=session_maker,
            gateway=gateway,
            redis=redis,
            redis_key_prefix=redis_key_prefix,
        )
        if handled:
            return

    command = parse_command(event.text)
    if command is not None:
        if command.kind == "link":
            email = command.email
            assert email is not None

            async def build_link_response(
                _session: AsyncSession,
                _user_id: str | None,
            ) -> dict[str, Any]:
                return {
                    "kind": "link",
                    "im_user_id": event.sender_ref or event.sender_open_id or "",
                    "email": email,
                    "account_id": account.id,
                    "workspace_id": account.workspace_id,
                    "platform": "wecom",
                    "chat_id": event.channel_id,
                }

            result = await ingest_command_response(
                event,
                account=account,
                session_maker=session_maker,
                build_response=build_link_response,
            )
        else:
            from cubeplex.im.conversation_resolver import reset_im_conversation

            async def build_reset_response(
                session: AsyncSession,
                _user_id: str | None,
            ) -> dict[str, Any]:
                outcome = await reset_im_conversation(
                    session,
                    account_id=account.id,
                    channel_id=event.channel_id,
                    scope_key=event.scope_key,
                )
                return {"kind": "text", "text": format_reset_reply(outcome)}

            result = await ingest_command_response(
                event,
                account=account,
                session_maker=session_maker,
                build_response=build_reset_response,
                require_current_identity=True,
            )
        if result.receipt_id is not None and event.reply_to_id is not None:
            await _deliver_command_response(
                receipt_id=result.receipt_id,
                req_id=event.reply_to_id,
                chat_id=event.channel_id,
                session_maker=session_maker,
                gateway=gateway,
            )
        return

    ingest_result = await ingest_inbound_event(
        event,
        account=account,
        session_maker=session_maker,
        identity_resolver=NullIdentityResolver(),
        rejection_notifier=connector,
    )
    logger.info(
        "[WeCom] inbound {}: {}",
        event.platform_event_id,
        ingest_result.outcome,
    )
