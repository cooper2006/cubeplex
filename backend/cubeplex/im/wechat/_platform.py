"""WeChatPlatform — PlatformConnector implementation for WeChat (iLink)."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class WeChatPlatform:
    """PlatformConnector for WeChat (iLink long-polling + QR binding)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubeplex.im.wechat.connector import WeChatConnector

        connector = WeChatConnector()
        return connector.parse_ilink_message(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        from cubeplex.im.outbound import OutboundRunTailer
        from cubeplex.im.types import RenderState
        from cubeplex.im.wechat.connector import WeChatConnector
        from cubeplex.im.wechat.renderer import WeChatOpDispatcher

        app = kwargs["app"]
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        load_secrets = kwargs.get("load_secrets")

        # Get bot_token from credentials
        bot_token = ""
        gw = gateways.get(account.id)
        if gw is not None:
            bot_token = getattr(gw, "_bot_token", "")

        if not bot_token and load_secrets is not None:
            secrets_data = await load_secrets(account)
            bot_token = str(secrets_data.get("bot_token") or "")

        connector = WeChatConnector(
            chat_id=queue_item.channel_id,
            context_token=queue_item.reply_to_id,
        )

        cfg = account.config or {}
        state = RenderState(
            bot_name=cfg.get("bot_name") or "CubePlex",
            run_id=run_id,
            reply_to_id=queue_item.reply_to_id,
            inbound_message_id=queue_item.inbound_message_id,
            stream_interval=1.0,
        )

        op_dispatcher = WeChatOpDispatcher(connector=connector, state=state)

        from cubeplex.config import config
        from cubeplex.im.artifacts import IMArtifactDispatcher

        public_base = str(config.get("api.public_url", "") or "")
        artifact_disp = IMArtifactDispatcher(
            connector=connector,
            redis=app.state.redis,
            redis_key_prefix=app.state.redis_key_prefix,
            public_base_url=public_base,
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            conversation_id=queue_item.conversation_id,
            card_state=state.card_state,
            run_id=run_id,
            platform="wechat",
            chat_id=queue_item.channel_id,
            reply_to_id=queue_item.reply_to_id,
            supports_inline_image=False,
        )

        shared_mode = False
        _sm = kwargs.get("session_maker")
        if _sm is not None:
            from cubeplex.im.types import is_shared_mode_for_tailer

            shared_mode = await is_shared_mode_for_tailer(
                _sm,
                queue_item.account_id,
                queue_item.channel_id,
                queue_item.conversation_id,
            )

        tailer = OutboundRunTailer(
            redis=app.state.redis,
            key_prefix=app.state.redis_key_prefix,
            run_id=run_id,
            connector=connector,
            state=state,
            dispatcher=op_dispatcher,
            artifact_dispatcher=artifact_disp,
            responder_open_id=queue_item.sender_open_id,
            shared_mode=shared_mode,
        )
        asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        from cubeplex.im.inbound import ingest_inbound_event
        from cubeplex.im.wechat.gateway import WeChatGateway

        secrets: dict[str, Any] = kwargs.get("secrets", {})
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        session_maker = kwargs.get("session_maker")
        run_manager = kwargs.get("run_manager")
        redis = kwargs.get("redis")
        redis_key_prefix: str = kwargs.get("redis_key_prefix", "")

        # app gives us the same encryption backend used by the credential vault,
        # needed to persist the bot_token once QR binding completes.
        app = kwargs.get("app")
        encryption_backend = getattr(app, "state", None) and getattr(app.state, "encryption_backend", None)

        bot_token = str(secrets.get("bot_token") or "")
        qrcode_login_enabled = bool(secrets.get("qrcode_login_enabled", False))

        if not bot_token and not qrcode_login_enabled:
            logger.warning("[WeChat] skipping account %s — missing bot_token and qrcode_login", account.id)
            return

        existing = gateways.get(account.id)
        if existing is not None and existing.is_open:
            existing_qrcode = getattr(existing, "_qrcode_login_enabled", False)
            if existing_qrcode == qrcode_login_enabled:
                logger.info("[WeChat] reusing existing gateway for account %s", account.id)
                return

        if existing is not None:
            logger.info("[WeChat] stopping stale gateway for account %s", account.id)
            await existing.stop()

        gw = WeChatGateway(
            account=account,
            bot_token=bot_token,
            qrcode_login_enabled=qrcode_login_enabled,
            ingest=ingest_inbound_event,
            session_maker=session_maker,
            run_manager=run_manager,
            redis=redis,
            redis_key_prefix=redis_key_prefix,
            encryption_backend=encryption_backend,
        )
        await gw.start()
        gateways[account.id] = gw

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        gateways: dict[str, Any] = kwargs.get("gateways", {})
        gw = gateways.pop(account.id, None)
        if gw is not None:
            await gw.stop()
