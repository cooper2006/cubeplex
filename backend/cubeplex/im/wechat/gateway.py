"""WeChat gateway — iLink long-polling with QR code binding support."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import padding as pad_module
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# iLink helpers (ported from baseagent)
# ---------------------------------------------------------------------------

def _build_ilink_client_version(version: str) -> str:
    parts = [part.strip() for part in version.split(".")]

    def _part(index: int) -> int:
        if index >= len(parts):
            return 0
        try:
            return max(0, min(int(parts[index] or 0), 0xFF))
        except ValueError:
            return 0

    major = _part(0)
    minor = _part(1)
    patch = _part(2)
    return str((major << 16) | (minor << 8) | patch)


def _build_wechat_uin() -> str:
    return base64.b64encode(str(secrets.randbits(32)).encode("utf-8")).decode("utf-8")


def _encrypt_aes_128_ecb(content: bytes, key: bytes) -> bytes:
    if len(key) != 16:
        raise ValueError("AES-128-ECB requires 16-byte key")
    padder = pad_module.PKCS7(128).padder()
    padded = padder.update(content) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    return cipher.encryptor().update(padded) + cipher.encryptor().finalize()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# WeChatGateway
# ---------------------------------------------------------------------------

class WeChatGateway:
    """WeChat iLink gateway with QR code binding support.

    Connects to ilinkai.weixin.qq.com using long-polling.
    Supports QR code bootstrap when bot_token is not yet set.
    """

    DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
    DEFAULT_CHANNEL_VERSION = "1.0"
    DEFAULT_POLLING_TIMEOUT = 35.0
    DEFAULT_RETRY_DELAY = 5.0
    DEFAULT_QRCODE_POLL_INTERVAL = 2.0
    DEFAULT_QRCODE_POLL_TIMEOUT = 180.0
    DEFAULT_QRCODE_BOT_TYPE = 3

    def __init__(
        self,
        *,
        account: Any,
        bot_token: str = "",
        qrcode_login_enabled: bool = False,
        base_url: str = "",
        ingest: Any,
        session_maker: Any,
        run_manager: Any,
        redis: Any,
        redis_key_prefix: str,
        encryption_backend: Any = None,
    ) -> None:
        self._account = account
        self._bot_token = bot_token.strip() if isinstance(bot_token, str) else ""
        self._qrcode_login_enabled = qrcode_login_enabled
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._channel_version = self.DEFAULT_CHANNEL_VERSION
        self._polling_timeout = self.DEFAULT_POLLING_TIMEOUT
        self._retry_delay = self.DEFAULT_RETRY_DELAY
        self._qrcode_poll_interval = self.DEFAULT_QRCODE_POLL_INTERVAL
        self._qrcode_poll_timeout = self.DEFAULT_QRCODE_POLL_TIMEOUT
        self._qrcode_bot_type = self.DEFAULT_QRCODE_BOT_TYPE
        self._ilink_app_id = ""
        self._ilink_bot_id: str | None = None
        self._get_updates_buf = ""
        self._context_tokens_by_chat: dict[str, str] = {}
        self._context_tokens_by_thread: dict[str, str] = {}
        self._auth_state: dict[str, Any] = {}

        self._client: httpx.AsyncClient | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._qr_auth_task: asyncio.Task[None] | None = None
        self._auth_lock = asyncio.Lock()
        self._stopping = False

        self._ingest = ingest
        self._session_maker = session_maker
        self._run_manager = run_manager
        self._redis = redis
        self._redis_key_prefix = redis_key_prefix
        self._encryption_backend = encryption_backend

    @property
    def is_open(self) -> bool:
        return not self._stopping and (self._bot_token or self._qrcode_login_enabled)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the iLink long-polling loop."""
        if not self._bot_token and not self._qrcode_login_enabled:
            logger.error("[WeChat] requires bot_token or qrcode_login_enabled")
            return

        self._client = httpx.AsyncClient(timeout=max(self._polling_timeout + 5.0, 10.0))
        self._stopping = False

        # Start polling loop
        self._poll_task = asyncio.create_task(self._poll_loop(), name="wechat-poll")
        self._poll_task.add_done_callback(self._on_poll_task_done)

        # If no token yet, start QR auth loop
        if not self._bot_token and self._qrcode_login_enabled:
            self._qr_auth_task = asyncio.create_task(self._qr_auth_loop(), name="wechat-qr-auth")

        logger.info("[WeChat] gateway started for account %s", self._account.id)

    async def stop(self) -> None:
        """Stop the gateway."""
        self._stopping = True
        for task in (self._poll_task, self._qr_auth_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("[WeChat] gateway stopped for account %s", self._account.id)

    def _on_poll_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("[WeChat] poll task failed: %s", exc)

    # ------------------------------------------------------------------
    # QR Auth loop
    # ------------------------------------------------------------------

    async def _qr_auth_loop(self) -> None:
        """Poll QR status until user scans and confirms."""
        while not self._stopping:
            try:
                authenticated = await self._ensure_authenticated()
                if authenticated:
                    return
            except Exception:
                logger.debug("[WeChat] QR auth retrying...")
            await asyncio.sleep(self._retry_delay)

    async def _ensure_authenticated(self) -> bool:
        async with self._auth_lock:
            if self._bot_token:
                return True
            if not self._qrcode_login_enabled:
                return False
            try:
                await self._bind_via_qrcode()
            except Exception:
                logger.exception("[WeChat] QR binding failed")
                return False
            return bool(self._bot_token)

    async def _bind_via_qrcode(self) -> None:
        stored_qrcode = str(self._auth_state.get("qrcode") or "").strip()
        stored_status = str(self._auth_state.get("status") or "").strip().lower()
        if stored_qrcode and stored_status == "pending":
            qrcode = stored_qrcode
            qrcode_img_content = str(self._auth_state.get("qrcode_img_content") or "").strip()
            logger.info("[WeChat] reusing pending QR for bind poll. qrcode=%s", qrcode)
        else:
            data = await self._request_public_get_json(
                "/ilink/bot/get_bot_qrcode",
                params={"bot_type": self._qrcode_bot_type},
            )
            qrcode = str(data.get("qrcode") or "").strip()
            if not qrcode:
                raise RuntimeError("iLink get_bot_qrcode did not return qrcode")
            qrcode_img_content = str(data.get("qrcode_img_content") or "").strip()
            self._save_auth_state(
                status="pending",
                qrcode=qrcode,
                qrcode_img_content=qrcode_img_content or None,
                qr_generated_at=time.time(),
            )
            logger.info("[WeChat] QR login required. qrcode=%s", qrcode)

        deadline = time.monotonic() + max(self._qrcode_poll_timeout, 1.0)
        while time.monotonic() < deadline:
            status_data = await self._request_public_get_json(
                "/ilink/bot/get_qrcode_status",
                params={"qrcode": qrcode},
                timeout=self._qrcode_poll_timeout,
            )
            status = str(status_data.get("status") or "").strip().lower()
            logger.info("[WeChat] QR status: %s", status)
            if status == "confirmed":
                token = str(status_data.get("bot_token") or "").strip()
                if not token:
                    raise RuntimeError("QR confirmed without bot_token")
                self._bot_token = token
                self._ilink_bot_id = str(status_data.get("ilink_bot_id") or "").strip() or None
                self._save_auth_state(status="confirmed", bot_token=token)
                return
            if status in {"expired", "canceled", "cancelled", "invalid", "failed"}:
                # Only record the failure for the QR we actually polled. A
                # newer pending QR (written by the connect endpoint) must stay
                # pending so the next auth attempt reuses it instead.
                if self._auth_state.get("qrcode") == qrcode:
                    self._save_auth_state(
                        status=status,
                        qrcode=qrcode,
                        qrcode_img_content=qrcode_img_content or None,
                    )
                raise RuntimeError(f"iLink QR code flow ended with status={status}")
            await asyncio.sleep(self._qrcode_poll_interval)

        if self._auth_state.get("qrcode") == qrcode:
            self._save_auth_state(
                status="timeout",
                qrcode=qrcode,
                qrcode_img_content=qrcode_img_content or None,
            )
        raise TimeoutError("Timed out waiting for WeChat QR confirmation")

    def _wake_qr_auth(self) -> None:
        if self._bot_token or self._stopping:
            return
        if self._qr_auth_task is not None and not self._qr_auth_task.done():
            self._qr_auth_task.cancel()
            try:
                self._qr_auth_task.result()
            except (asyncio.CancelledError, Exception):
                pass
            self._qr_auth_task = None
        self._qr_auth_task = asyncio.create_task(self._qr_auth_loop(), name="wechat-qr-auth")

    # ------------------------------------------------------------------
    # QR URL getters (for API)
    # ------------------------------------------------------------------

    async def get_qrcode_url(self) -> str | None:
        """Get current QR code URL for binding. Returns None if already authenticated.

        Checks the iLink server for QR status before returning — if the stored
        QR has expired server-side (but our state hasn't caught up yet), a fresh
        one is generated and the gateway's auth state is updated so polling
        continues against the same code the user sees.
        """
        if self._bot_token:
            return None
        url = str(self._auth_state.get("qrcode_img_content") or "").strip() or None
        stored_qrcode = str(self._auth_state.get("qrcode") or "").strip()
        status = str(self._auth_state.get("status") or "").strip().lower()
        if not url or status in {"expired", "canceled", "cancelled", "invalid", "failed", "timeout"}:
            url = await self._request_qrcode()
        elif stored_qrcode and url:
            # Verify the stored QR is still valid on the iLink server.
            # The server may have expired it while we were polling, but
            # our auth_state status hasn't been updated yet.
            # If we can't reach the server, fall through to return the
            # existing URL rather than raising — the gateway is already
            # polling this QR and the user sees it.
            try:
                status_data = await self._request_public_get_json(
                    "/ilink/bot/get_qrcode_status",
                    params={"qrcode": stored_qrcode},
                    timeout=5.0,
                )
                server_status = str(status_data.get("status") or "").strip().lower()
                if server_status in {"expired", "canceled", "cancelled", "invalid", "failed"}:
                    logger.info(
                        "[WeChat] stored QR %s expired on server (status=%s), refreshing",
                        stored_qrcode[:12],
                        server_status,
                    )
                    url = await self._request_qrcode()
            except Exception:
                logger.debug(
                    "[WeChat] failed to verify QR status, reusing existing QR %s",
                    stored_qrcode[:12],
                    exc_info=True,
                )
        if url:
            self._wake_qr_auth()
        return url

    def get_qr_generated_at(self) -> float | None:
        """Return the Unix timestamp when the current QR code was generated, or None."""
        raw = self._auth_state.get("qr_generated_at")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _make_ilink_qr_url(qrcode: str) -> str:
        """Build the WeChat iLink activation URL from a qrcode string."""
        return f"https://liteapp.weixin.qq.com/q/7GiQu1?qrcode={qrcode}&bot_type={WeChatGateway.DEFAULT_QRCODE_BOT_TYPE}"

    async def force_get_qrcode_url(self) -> str | None:
        """Generate a fresh QR code URL, ignoring existing state."""
        if self._bot_token:
            return None
        return await self._request_qrcode()

    def sync_qrcode(self, qrcode: str, qrcode_img_content: str) -> None:
        """Sync a QR code generated by the connect endpoint into this gateway's auth state.

        Ensures the gateway's _qr_auth_loop polls the same QR the user is
        looking at, even when the connect endpoint had to generate the QR
        before the gateway was fully started.
        """
        self._save_auth_state(
            status="pending",
            qrcode=qrcode,
            qrcode_img_content=qrcode_img_content,
            qr_generated_at=time.time(),
        )
        self._wake_qr_auth()

    async def _request_qrcode(self) -> str | None:
        try:
            data = await self._request_public_get_json(
                "/ilink/bot/get_bot_qrcode",
                params={"bot_type": self._qrcode_bot_type},
            )
        except Exception:
            logger.exception("[WeChat] failed to request QR")
            return None
        qrcode_str = str(data.get("qrcode") or "").strip()
        if not qrcode_str:
            return None
        img_content = str(data.get("qrcode_img_content") or "").strip() or None
        self._save_auth_state(status="pending", qrcode=qrcode_str, qrcode_img_content=img_content)
        return self._make_ilink_qr_url(qrcode_str)

    # ------------------------------------------------------------------
    # Binding code helpers
    # ------------------------------------------------------------------

    def _extract_connect_code(self, text: str) -> str | None:
        """Extract binding code from /connect <code> message."""
        parts = text.strip().split()
        if len(parts) < 2:
            return None
        cmd = parts[0].lower()
        if cmd in {"/connect", "connect"}:
            return parts[1]
        return None

    async def _check_binding_code(self, code: str, chat_id: str, context_token: str) -> bool:
        """Check if message contains valid binding code. Returns True if handled."""
        if not code or self._redis is None:
            return False

        key = f"{self._redis_key_prefix}:wechat:binding:{code}"
        raw = await self._redis.get(key)
        if not raw:
            await self._send_connection_reply(chat_id, context_token, "连接码无效或已过期。")
            return True

        # Atomic delete
        deleted = await self._redis.delete(key)
        if not deleted:
            return False

        import json
        payload = json.loads(raw)
        account_id = payload.get("account_id")

        # Verify this code belongs to this gateway's account
        if account_id != self._account.id:
            await self._send_connection_reply(chat_id, context_token, "连接码无效或已过期。")
            return True

        # Check if we have bot_token (QR was scanned)
        if not self._bot_token:
            await self._send_connection_reply(chat_id, context_token, "请等待二维码绑定完成后再发送连接码。")
            return True

        # Update account with bot_token
        await self._update_account_token(self._bot_token, chat_id, context_token)
        await self._send_connection_reply(chat_id, context_token, "WeChat 连接成功！")
        return True

    async def _update_account_token(self, bot_token: str, chat_id: str, context_token: str) -> None:
        """Persist the bot_token from QR confirmation into the account credential."""
        try:
            async with self._session_maker() as session:
                from cubeplex.credentials.dependencies import build_credential_service

                if self._encryption_backend is None:
                    logger.error("[WeChat] encryption backend unavailable; cannot persist token")
                    return

                svc = build_credential_service(
                    session,
                    self._encryption_backend,
                    org_id=self._account.org_id,
                    actor_user_id=None,
                )
                # Rewrite the credential with the confirmed bot_token.
                await svc.update(
                    credential_id=self._account.credential_id,
                    plaintext=json.dumps(
                        {
                            "bot_token": bot_token,
                            "qrcode_login_enabled": True,
                        }
                    ),
                )

                # The account is now bound to the chat that scanned the QR.
                from sqlalchemy import update as sa_update
                from cubeplex.models.im_connector import IMConnectorAccount

                await session.execute(
                    sa_update(IMConnectorAccount)
                    .where(IMConnectorAccount.id == self._account.id)  # type: ignore[arg-type]
                    .values(external_account_id=chat_id)
                )
                await session.commit()
                logger.info("[WeChat] account %s now bound to user %s", self._account.id, chat_id)
        except Exception:
            logger.exception("[WeChat] failed to update account token")

    async def _send_connection_reply(self, chat_id: str, context_token: str, text: str) -> None:
        """Send a confirmation message to the user."""
        if not context_token or not self._bot_token:
            return
        await self.send_text(chat_id, context_token, text)

    # ------------------------------------------------------------------
    # Long-polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while not self._stopping:
            if not self._bot_token:
                await asyncio.sleep(self._retry_delay)
                continue
            try:
                data = await self._request_json(
                    "/ilink/bot/getupdates",
                    {
                        "get_updates_buf": self._get_updates_buf,
                        "base_info": self._base_info(),
                    },
                    timeout=max(self._polling_timeout + 5.0, 10.0),
                )
                ret = data.get("ret", 0)
                if ret not in (0, None):
                    errcode = data.get("errcode")
                    if errcode == -14:
                        self._bot_token = ""
                        self._get_updates_buf = ""
                        self._save_auth_state(status="expired", bot_token="")
                        logger.error("[WeChat] bot token expired")
                        break
                    logger.warning("[WeChat] getupdates ret=%s errcode=%s", ret, errcode)
                    await asyncio.sleep(self._retry_delay)
                    continue
                next_buf = data.get("get_updates_buf")
                if isinstance(next_buf, str) and next_buf != self._get_updates_buf:
                    self._get_updates_buf = next_buf
                for raw_msg in data.get("msgs", []):
                    await self._handle_update(raw_msg)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[WeChat] polling loop failed")
                await asyncio.sleep(self._retry_delay)

    async def _handle_update(self, raw_message: Any) -> None:
        if not isinstance(raw_message, dict):
            return
        if raw_message.get("message_type") != 1:
            return

        chat_id = str(raw_message.get("from_user_id") or raw_message.get("ilink_user_id") or "").strip()
        if not chat_id:
            return

        text = self._extract_text(raw_message)
        context_token = str(raw_message.get("context_token") or "").strip()

        # Handle binding code BEFORE normal message processing
        connect_code = self._extract_connect_code(text)
        if connect_code:
            handled = await self._check_binding_code(connect_code, chat_id, context_token)
            if handled:
                return

        if context_token:
            self._context_tokens_by_chat[chat_id] = context_token
            thread_ts = str(raw_message.get("client_id") or raw_message.get("msg_id") or "").strip() or None
            if thread_ts:
                self._context_tokens_by_thread[thread_ts] = context_token

        from cubeplex.im.wechat.connector import WeChatConnector
        connector = WeChatConnector()
        event = connector.parse_ilink_message(raw_message, chat_id=chat_id)
        if event is None:
            return

        from cubeplex.im.identity import NullIdentityResolver

        await self._ingest(
            event=event,
            account=self._account,
            session_maker=self._session_maker,
            identity_resolver=NullIdentityResolver(),
            rejection_notifier=None,
        )

    # ------------------------------------------------------------------
    # Outbound helpers
    # ------------------------------------------------------------------

    async def send_text(self, chat_id: str, context_token: str, text: str) -> bool:
        if not self._bot_token:
            return False
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": chat_id,
                "client_id": f"cubeplex_{int(time.time() * 1000)}_{secrets.token_hex(2)}",
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [
                    {"type": 1, "text_item": {"text": text}},
                ],
            },
            "base_info": self._base_info(),
        }
        try:
            data = await self._request_json("/ilink/bot/sendmessage", payload)
            return data.get("ret") == 0
        except Exception:
            logger.exception("[WeChat] send failed")
            return False

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _request_json(self, path: str, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        assert self._client is not None
        resp = await self._client.post(
            f"{self._base_url}{path}",
            json=payload,
            headers=self._auth_headers(),
            timeout=timeout or 60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    async def _request_public_get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        assert self._client is not None
        resp = await self._client.get(
            f"{self._base_url}{path}",
            params=params,
            headers=self._public_headers(),
            timeout=timeout or 15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def _base_info(self) -> dict[str, str]:
        return {"channel_version": self._channel_version}

    def _common_headers(self) -> dict[str, str]:
        headers = {
            "iLink-App-ClientVersion": _build_ilink_client_version(self._channel_version),
            "X-WECHAT-UIN": _build_wechat_uin(),
        }
        if self._ilink_app_id:
            headers["iLink-App-Id"] = self._ilink_app_id
        return headers

    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._bot_token}",
            "AuthorizationType": "ilink_bot_token",
            **self._common_headers(),
        }
        return headers

    def _public_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            **self._common_headers(),
        }

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _save_auth_state(self, **kwargs: Any) -> dict[str, Any]:
        self._auth_state.update(kwargs)
        return self._auth_state

    # ------------------------------------------------------------------
    # Message parsing helpers
    # ------------------------------------------------------------------

    def _extract_text(self, raw: dict[str, Any]) -> str:
        item_list = raw.get("item_list") or []
        for item in item_list:
            if isinstance(item, dict) and item.get("type") == 1:
                text_item = item.get("text_item") or {}
                return str(text_item.get("text") or "").strip()
        return ""
