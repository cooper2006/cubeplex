"""Workspace-scope IM connector routes (Task 15)."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.routes.v1._im_runtime import build_im_list_out
from cubeplex.api.schemas.im_connector import (
    ConnectDingtalkAccountIn,
    ConnectDiscordAccountIn,
    ConnectFeishuAccountIn,
    ConnectIMAccountIn,
    ConnectSlackAccountIn,
    ConnectTeamsAccountIn,
    ConnectWeChatAccountIn,
    ConnectWecomAccountIn,
    DingtalkAppsIn,
    DingtalkAppsOut,
    IdentityLinkListOut,
    IdentityLinkOut,
    IMAccountListOut,
    IMAccountOut,
    ImRuntimeStatus,
    WeChatConnectOut,
    WeComConnectOut,
)
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.credentials.dependencies import (
    build_credential_service,
    get_encryption_backend,
)
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db.session import get_session
from cubeplex.im.bot_settings import IMBotSettings, load_bot_settings
from cubeplex.models.im_connector import IMConnectorAccount, IMIdentityLink
from cubeplex.models.membership import Role
from cubeplex.models.user import User
from cubeplex.repositories.membership import MembershipRepository
from cubeplex.repositories.organization_membership import OrganizationMembershipRepository
from cubeplex.services.im_connector import IMConnectorService
from cubeplex.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/im", tags=["ws-im"])


def _service(
    session: AsyncSession,
    backend: EncryptionBackend,
    ctx: RequestContext,
) -> IMConnectorService:
    creds = build_credential_service(session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id)
    return IMConnectorService(session, creds, org_id=ctx.org_id)


def _to_out(account: IMConnectorAccount) -> IMAccountOut:
    cfg = account.config or {}
    return IMAccountOut(
        id=account.id,
        platform=account.platform,
        external_account_id=account.external_account_id,
        workspace_id=account.workspace_id,
        acting_user_id=account.acting_user_id,
        delivery_mode=account.delivery_mode,
        enabled=account.enabled,
        runtime=ImRuntimeStatus.unknown(),
        bot_app_name=cfg.get("bot_app_name") or None,
        bot_avatar_url=cfg.get("bot_avatar_url") or None,
    )


async def _resolve_acting_user(
    acting_user_id: str,
    ctx: RequestContext,
    session: AsyncSession,
) -> str:
    # ``"self"`` is always allowed: the caller binds a bot that runs as
    # themselves. Any other value is impersonation — the bound bot would
    # run with someone else's permissions for every future IM-triggered
    # message that isn't covered by the per-sender identity gate. We
    # require **workspace admin** to grant that (the identity gate falls
    # back to ``acting_user_id`` when the sender doesn't resolve to a
    # workspace member, so an org-member-only check leaks privilege).
    if acting_user_id == "self":
        return ctx.user.id
    caller_ws_role = await MembershipRepository(session).get_role(
        user_id=ctx.user.id, workspace_id=ctx.workspace_id
    )
    if caller_ws_role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace admin required to impersonate another user",
        )
    om_repo = OrganizationMembershipRepository(session)
    target_org_role = await om_repo.get_role(user_id=acting_user_id, org_id=ctx.org_id)
    if target_org_role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="acting_user_id is not a member of this organization",
        )
    return acting_user_id


async def _connect_feishu(
    body: ConnectFeishuAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_feishu(
            workspace_id=ctx.workspace_id,
            app_id=body.app_id,
            app_secret=body.app_secret,
            encrypt_key=body.encrypt_key,
            verification_token=body.verification_token,
            domain=body.domain,
            delivery_mode=body.delivery_mode,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if account.delivery_mode == "long_connection" and account.enabled:
        starter = getattr(request.app.state, "im_connect_account", None)
        if starter is not None:
            try:
                await starter(account)
            except Exception:
                logger.opt(exception=True).warning(
                    "[IM ws] long-connection startup failed for {}", account.id
                )
    return _to_out(account)


async def _connect_discord(
    body: ConnectDiscordAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_discord(
            workspace_id=ctx.workspace_id,
            bot_token=body.bot_token,
            application_id=body.application_id,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] discord gateway startup failed for {}", account.id
            )
    return _to_out(account)


async def _connect_slack(
    body: ConnectSlackAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_slack(
            workspace_id=ctx.workspace_id,
            bot_token=body.bot_token,
            app_token=body.app_token,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] slack gateway startup failed for {}", account.id
            )
    return _to_out(account)


async def _connect_dingtalk(
    body: ConnectDingtalkAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_dingtalk(
            workspace_id=ctx.workspace_id,
            app_key=body.app_key,
            app_secret=body.app_secret,
            bot_name=body.bot_name,
            bot_avatar_url=body.bot_avatar_url,
            acting_user_id=acting,
        )
    except ValueError as exc:
        msg = str(exc)
        code = status.HTTP_409_CONFLICT if "already exists" in msg else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=msg) from exc
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] dingtalk gateway startup failed for {}",
                account.id,
            )
    return _to_out(account)


async def _connect_wechat(
    body: ConnectWeChatAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_wechat(
            workspace_id=ctx.workspace_id,
            bot_token=body.bot_token,
            qrcode_login=body.qrcode_login,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.opt(exception=True).warning("[IM ws] wechat app init failed for {}", account.id)
    return _to_out(account)


async def _connect_teams(
    body: ConnectTeamsAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_teams(
            workspace_id=ctx.workspace_id,
            app_id=body.app_id,
            app_secret=body.app_secret,
            tenant_id=body.tenant_id,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.opt(exception=True).warning("[IM ws] teams app init failed for {}", account.id)
    return _to_out(account)


async def _connect_wecom(
    body: ConnectWecomAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    from cubeplex.im.wecom.gateway import WecomAuthenticationError, WecomUnavailableError

    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_wecom(
            workspace_id=ctx.workspace_id,
            bot_id=body.bot_id,
            bot_name=body.bot_name,
            secret=body.secret,
            acting_user_id=acting,
        )
    except WecomAuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except WecomUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] WeCom gateway startup failed for {}",
                account.id,
            )
    return _to_out(account)


@router.post("/accounts", status_code=status.HTTP_201_CREATED, response_model=IMAccountOut)
async def connect_account(
    workspace_id: str,
    body: ConnectIMAccountIn,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")

    if isinstance(body, ConnectFeishuAccountIn):
        return await _connect_feishu(body, request, ctx, session, backend)
    elif isinstance(body, ConnectDiscordAccountIn):
        return await _connect_discord(body, request, ctx, session, backend)
    elif isinstance(body, ConnectSlackAccountIn):
        return await _connect_slack(body, request, ctx, session, backend)
    elif isinstance(body, ConnectDingtalkAccountIn):
        return await _connect_dingtalk(body, request, ctx, session, backend)
    elif isinstance(body, ConnectTeamsAccountIn):
        return await _connect_teams(body, request, ctx, session, backend)
    elif isinstance(body, ConnectWecomAccountIn):
        return await _connect_wecom(body, request, ctx, session, backend)
    elif isinstance(body, ConnectWeChatAccountIn):
        return await _connect_wechat(body, request, ctx, session, backend)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported platform",
        )


@router.post("/dingtalk/apps", response_model=DingtalkAppsOut)
async def list_dingtalk_apps(
    workspace_id: str,
    body: DingtalkAppsIn,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> DingtalkAppsOut:
    """Validate DingTalk credentials and return the org's app list for the picker."""
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    try:
        apps = await IMConnectorService.list_dingtalk_apps(
            app_key=body.app_key,
            app_secret=body.app_secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    from cubeplex.api.schemas.im_connector import DingtalkAppInfo

    return DingtalkAppsOut(
        apps=[DingtalkAppInfo(**a) for a in apps],
    )


@router.get("/accounts", response_model=IMAccountListOut)
async def list_accounts(
    workspace_id: str,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountListOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    svc = _service(session, backend, ctx)
    accounts = await svc.list_for_workspace(workspace_id=ctx.workspace_id)
    long_conns = getattr(request.app.state, "im_long_connections", None) or {}
    gateways = getattr(request.app.state, "im_gateways", None) or {}
    return await build_im_list_out(
        svc=svc,
        session=session,
        long_conns=long_conns,
        gateways=gateways,
        redis=request.app.state.redis,
        redis_key_prefix=request.app.state.redis_key_prefix,
        accounts=accounts,
    )


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    workspace_id: str,
    account_id: str,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> None:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    svc = _service(session, backend, ctx)
    # Pass workspace_id so a member of workspace A cannot delete an account
    # that lives in workspace B within the same org.
    await svc.delete(account_id=account_id, workspace_id=ctx.workspace_id)
    # Tear down any live connection so a deleted account stops accepting
    # events immediately, not after the next API restart.
    runtime_disconnect = getattr(request.app.state, "im_disconnect_account", None)
    if runtime_disconnect is not None:
        await runtime_disconnect(account_id)
    long_conns = getattr(request.app.state, "im_long_connections", None) or {}
    lc = long_conns.pop(account_id, None)
    if lc is not None:
        try:
            await lc.disconnect()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] long-connection disconnect failed on delete for {}",
                account_id,
            )
    gateways = getattr(request.app.state, "im_gateways", None) or {}
    gw = gateways.pop(account_id, None)
    if gw is not None:
        try:
            await gw.stop()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] gateway stop failed on delete for {}",
                account_id,
            )


@router.post("/accounts/{account_id}/disable", response_model=IMAccountOut)
async def disable_workspace_account(
    workspace_id: str,
    account_id: str,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    """Workspace-scope disable. The admin route remains for org-wide ops."""
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    role = await MembershipRepository(session).get_role(
        user_id=ctx.user.id, workspace_id=ctx.workspace_id
    )
    if role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace admin required",
        )
    svc = _service(session, backend, ctx)
    account = await svc.get(account_id=account_id, workspace_id=ctx.workspace_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    updated = await svc.set_enabled(account_id=account_id, enabled=False)
    assert updated is not None
    # Drop any live connection so the bot stops responding immediately.
    runtime_disconnect = getattr(request.app.state, "im_disconnect_account", None)
    if runtime_disconnect is not None:
        await runtime_disconnect(account_id)
    long_conns = getattr(request.app.state, "im_long_connections", None) or {}
    lc = long_conns.pop(account_id, None)
    if lc is not None:
        try:
            await lc.disconnect()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] long-conn disconnect failed on disable for {}",
                account_id,
            )
    gateways = getattr(request.app.state, "im_gateways", None) or {}
    gw = gateways.pop(account_id, None)
    if gw is not None:
        try:
            await gw.stop()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] gateway stop failed on disable for {}",
                account_id,
            )
    return _to_out(updated)


@router.post("/accounts/{account_id}/enable", response_model=IMAccountOut)
async def enable_workspace_account(
    workspace_id: str,
    account_id: str,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    """Workspace-scope enable. Spins up the long-conn inline."""
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    role = await MembershipRepository(session).get_role(
        user_id=ctx.user.id, workspace_id=ctx.workspace_id
    )
    if role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace admin required",
        )
    svc = _service(session, backend, ctx)
    account = await svc.get(account_id=account_id, workspace_id=ctx.workspace_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    updated = await svc.set_enabled(account_id=account_id, enabled=True)
    assert updated is not None
    if updated.delivery_mode in ("long_connection", "gateway", "stream"):
        starter = getattr(request.app.state, "im_connect_account", None)
        if starter is not None:
            try:
                await starter(updated)
            except Exception:
                logger.opt(exception=True).warning(
                    "[IM ws] long-conn startup failed on enable for {}",
                    account_id,
                )
    return _to_out(updated)


# ---------------------------------------------------------------------------
# Account-level bot settings (routing + topic mode)
# ---------------------------------------------------------------------------


@router.get("/accounts/{account_id}/settings", response_model=IMBotSettings)
async def get_bot_settings(
    workspace_id: str,
    account_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMBotSettings:
    """Read the bot's account-level routing/topic settings (defaults if unset)."""
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    svc = _service(session, backend, ctx)
    account = await svc.get(account_id=account_id, workspace_id=ctx.workspace_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return load_bot_settings(account.config)


@router.put("/accounts/{account_id}/settings", response_model=IMBotSettings)
async def update_bot_settings(
    workspace_id: str,
    account_id: str,
    body: IMBotSettings,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMBotSettings:
    """Update the bot's routing/topic settings. Admin-only (mutates behavior)."""
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    role = await MembershipRepository(session).get_role(
        user_id=ctx.user.id, workspace_id=ctx.workspace_id
    )
    if role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace admin required",
        )
    if body.routing_mode == "shared" and body.sandbox_mode is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sandbox_mode is required when routing_mode is shared",
        )
    svc = _service(session, backend, ctx)
    account = await svc.get(account_id=account_id, workspace_id=ctx.workspace_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    # Teams' connector only emits per-sender scopes, so shared routing would
    # silently make one group conversation per sender. Reject it on the API
    # too, not just in the UI.
    if body.routing_mode == "shared" and account.platform == "teams":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="shared routing is not supported for this platform",
        )
    updated = await svc.update_bot_settings(
        account_id=account_id, settings=body, workspace_id=ctx.workspace_id
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return load_bot_settings(updated.config)


@router.get(
    "/accounts/{account_id}/identity-links",
    response_model=IdentityLinkListOut,
)
async def list_identity_links(
    workspace_id: str,
    account_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IdentityLinkListOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    rows = (
        await session.execute(
            select(  # type: ignore[call-overload]
                IMIdentityLink, User.email, User.display_name
            )
            .join(User, IMIdentityLink.user_id == User.id)
            .where(
                IMIdentityLink.account_id == account_id,
                IMIdentityLink.workspace_id == ctx.workspace_id,
            )
            .order_by(IMIdentityLink.created_at.desc())  # type: ignore[attr-defined]
        )
    ).all()
    return IdentityLinkListOut(
        links=[
            IdentityLinkOut(
                id=link.id,
                im_user_id=link.im_user_id,
                user_id=link.user_id,
                user_email=email,
                user_display_name=display_name or "",
                created_at=utc_isoformat(link.created_at),
            )
            for link, email, display_name in rows
        ]
    )


# ---------------------------------------------------------------------------
# WeChat QR code binding endpoints
# ---------------------------------------------------------------------------


def _wechat_ilink_headers() -> dict[str, str]:
    """Headers required by ilinkai.weixin.qq.com public endpoints."""
    import base64
    import secrets as _secrets

    uin = base64.b64encode(str(_secrets.randbits(32)).encode("utf-8")).decode("utf-8")
    return {
        "Content-Type": "application/json",
        "iLink-App-ClientVersion": "1",
        "X-WECHAT-UIN": uin,
    }


async def _fetch_wechat_qrcode() -> tuple[str, str] | None:
    """Fetch a fresh WeChat iLink QR and return (qrcode_string, ilink_url)."""
    import httpx

    params = {"bot_type": 3}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode",
            params=params,
            headers=_wechat_ilink_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
    qrcode = str(data.get("qrcode") or "").strip()
    if not qrcode:
        return None
    ilink_url = f"https://liteapp.weixin.qq.com/q/7GiQu1?qrcode={qrcode}&bot_type=3"
    return (qrcode, ilink_url)


async def _load_wechat_secret(
    account: IMConnectorAccount,
    *,
    session: AsyncSession,
    backend: EncryptionBackend,
    ctx: RequestContext,
) -> dict[str, Any]:
    """Decrypt a pending WeChat account's credential payload."""
    creds = build_credential_service(session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id)
    try:
        data = json.loads(
            await creds.get_decrypted(credential_id=account.credential_id, requesting_kind="im_bot")
        )
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def _ensure_wechat_qrcode_login(
    account: IMConnectorAccount,
    *,
    session: AsyncSession,
    backend: EncryptionBackend,
    ctx: RequestContext,
) -> None:
    """Re-arm QR login on a pending WeChat account.

    A pending account created without ``qrcode_login`` can never start a
    gateway (no bot token and no QR login), so re-binding against it hands
    back a QR that no gateway is polling — the user scans, sends
    ``/connect <code>``, and nothing ever answers. Flip the flag back on so
    an abandoned pending row becomes reusable.
    """
    data = await _load_wechat_secret(account, session=session, backend=backend, ctx=ctx)
    if data.get("qrcode_login_enabled") or data.get("bot_token"):
        return
    data["qrcode_login_enabled"] = True
    creds = build_credential_service(session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id)
    await creds.update(credential_id=account.credential_id, plaintext=json.dumps(data))
    await session.commit()
    logger.info("[IM ws] re-armed qrcode_login on pending wechat account {}", account.id)


@router.post(
    "/wechat/connect",
    status_code=status.HTTP_201_CREATED,
    response_model=WeChatConnectOut,
)
async def connect_wechat_qrcode(
    workspace_id: str,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> WeChatConnectOut:
    """Create a QR-code binding session for WeChat."""
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")

    svc = _service(session, backend, ctx)
    redis = getattr(request.app.state, "redis", None)
    key_prefix = getattr(request.app.state, "redis_key_prefix", "cubeplex")

    if not redis:
        raise HTTPException(status_code=503, detail="Redis is not available")

    import asyncio

    from cubeplex.models.im_connector import IMConnectorAccount

    _lock_key = f"{key_prefix}:wechat:connect-lock:{workspace_id}"
    _acquired = False
    for _retry in range(3):
        _acquired = await redis.set(_lock_key, "1", nx=True, ex=10)
        if _acquired:
            break
        await asyncio.sleep(0.3)

    if not _acquired:
        await asyncio.sleep(1.0)

    try:
        pending_rows = (
            (
                await session.execute(
                    select(IMConnectorAccount)
                    .where(
                        IMConnectorAccount.workspace_id == workspace_id,  # type: ignore[arg-type]
                        IMConnectorAccount.platform == "wechat",  # type: ignore[arg-type]
                        IMConnectorAccount.external_account_id.like(  # type: ignore[attr-defined]
                            "pending_%"
                        ),
                    )
                    .order_by(IMConnectorAccount.created_at.desc())  # type: ignore[attr-defined]
                )
            )
            .scalars()
            .all()
        )
        # Re-binding reuses exactly ONE pending account. Leftover pending rows
        # from an abandoned attempt would each get their own gateway, and the
        # /connect message would be handled by a gateway that does not own the
        # binding code on screen → "连接码无效或已过期".
        pending = pending_rows[0] if pending_rows else None
        stale = pending_rows[1:]
        if stale:
            live_gateways = getattr(request.app.state, "im_gateways", None) or {}
            for extra in stale:
                logger.info("[IM ws] removing duplicate pending wechat account {}", extra.id)
                stale_gw = live_gateways.pop(extra.id, None)
                if stale_gw is not None:
                    try:
                        await stale_gw.stop()
                    except Exception:
                        logger.opt(exception=True).warning(
                            "[IM ws] gateway stop failed for duplicate {}", extra.id
                        )
                await session.delete(extra)
            await session.commit()

        if pending is None:
            pending = await svc.connect_wechat(
                workspace_id=workspace_id,
                qrcode_login=True,
                acting_user_id=ctx.user.id,
            )
        else:
            await _ensure_wechat_qrcode_login(pending, session=session, backend=backend, ctx=ctx)
    finally:
        if _acquired:
            await redis.delete(_lock_key)

    gateways = getattr(request.app.state, "im_gateways", None) or {}
    existing_gw = gateways.get(pending.id)
    if existing_gw is None or not existing_gw.is_open:
        starter = getattr(request.app.state, "im_connect_account", None)
        if starter is not None:
            try:
                await starter(pending)
            except Exception:
                logger.opt(exception=True).warning(
                    "[IM ws] gateway startup failed for {}", pending.id
                )

    gateways = getattr(request.app.state, "im_gateways", None) or {}
    gw = gateways.get(pending.id)
    qrcode_url = None
    qrcode_string = None  # raw QR code string (not the URL)
    # Once the scan is confirmed the gateway holds a bot token. Minting a
    # fresh QR at that point would orphan the scan the token came from, so
    # keep showing the QR the user actually scanned.
    already_scanned = gw is not None and bool(getattr(gw, "is_authenticated", False))
    if gw is not None and already_scanned:
        qrcode_url = gw.last_qrcode_url()
    if gw is not None and not qrcode_url:
        # Re-initiating a bind must hand out a QR that is actually new —
        # reusing the previous one makes "I clicked 连接微信 again" look like
        # nothing happened.
        try:
            qrcode_url = await gw.force_get_qrcode_url()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] forced QR refresh failed for {}", pending.id
            )
    if gw is not None and not qrcode_url:
        try:
            qrcode_url = await gw.get_qrcode_url()
            # Extract raw QR code from URL for binding state storage
            if qrcode_url:
                import re as _re

                m = _re.search(r"qrcode=([^&]+)", qrcode_url)
                if m:
                    qrcode_string = m.group(1)
            logger.info("[IM ws] gateway QR URL for account {}: {}", pending.id, qrcode_url)
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] failed to get QR URL for account {}", pending.id
            )
    if not qrcode_url:
        for _ in range(6):
            await asyncio.sleep(0.5)
            gateways = getattr(request.app.state, "im_gateways", None) or {}
            gw = gateways.get(pending.id)
            if gw is not None:
                try:
                    qrcode_url = await gw.get_qrcode_url()
                    if qrcode_url:
                        import re as _re2

                        m2 = _re2.search(r"qrcode=([^&]+)", qrcode_url)
                        if m2:
                            qrcode_string = m2.group(1)
                    break
                except Exception:
                    pass
    if not qrcode_url:
        try:
            fetched = await _fetch_wechat_qrcode()
            if fetched is not None:
                qrcode_string, qrcode_url = fetched
                if gw is not None:
                    gw.sync_qrcode(qrcode_string, qrcode_url)
        except Exception:
            logger.opt(exception=True).warning("[IM ws] failed to fetch QR")

    if not qrcode_url:
        raise HTTPException(status_code=500, detail="Failed to generate QR code")

    # Ensure we have the raw QR code string for binding state
    if not qrcode_string and qrcode_url:
        import re as _re3

        m3 = _re3.search(r"qrcode=([^&]+)", qrcode_url)
        if m3:
            qrcode_string = m3.group(1)

    # Mint a fresh binding code for this connect attempt and invalidate the
    # previous one, so a code left on an abandoned page can't be redeemed.
    # The code we return must be one that is still live in Redis — reusing a
    # code we just deleted makes /connect answer "连接码无效或已过期".
    from cubeplex.im.wechat.binding_state import (
        create_binding_state,
        delete_binding_state,
        get_pending_binding,
        get_pending_binding_for_account,
        store_pending_binding,
    )

    previous_code = await get_pending_binding_for_account(
        redis, key_prefix=key_prefix, account_id=pending.id
    )
    keep_previous = False
    if previous_code and already_scanned:
        keep_previous = (
            await get_pending_binding(redis, key_prefix=key_prefix, code=previous_code) is not None
        )

    if keep_previous and previous_code:
        code = previous_code
    else:
        if previous_code:
            await delete_binding_state(redis, key_prefix=key_prefix, code=previous_code)
        code = await create_binding_state(
            redis, key_prefix=key_prefix, account_id=pending.id, qrcode=qrcode_string or qrcode_url
        )

    await store_pending_binding(redis, key_prefix=key_prefix, account_id=pending.id, code=code)
    logger.info("[IM ws] binding code={} for account={}", code, pending.id)

    return WeChatConnectOut(
        code=code,
        qrcode_url=qrcode_url,
        instruction="扫描二维码后，在微信中发送 /connect <code> 完成绑定",
        # iLink server-side QR expires in ~2 min; refresh well before that so
        # the user never scans a dead code (frontend counts down on this value).
        expires_in=110,
        qr_generated_at=gw.get_qr_generated_at() if gw is not None else None,
    )


# ---------------------------------------------------------------------------
# WeCom binding code endpoints
# ---------------------------------------------------------------------------


async def _connect_wecom_binding(
    body: ConnectWecomAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> WeComConnectOut:
    """Create a pending WeCom account and return a binding code.

    Unlike the original flow which validates credentials upfront and starts
    the gateway immediately, this version only persists the account and
    exposes a binding code. The gateway will be started by the runtime once
    the user sends ``/connect <code>`` from the WeCom client, at which point
    the binding code is consumed and the account is marked enabled.
    """
    from cubeplex.im.wecom.binding_state import (
        create_binding_state,
        get_pending_binding_for_account,
        store_pending_binding,
    )

    svc = _service(session, backend, ctx)
    redis = getattr(request.app.state, "redis", None)
    key_prefix = getattr(request.app.state, "redis_key_prefix", "cubeplex")

    if not redis:
        raise HTTPException(status_code=503, detail="Redis is not available")

    # Reuse a stale pending account if one exists for this workspace,
    # otherwise create a fresh one. The account stays disabled until the
    # binding code is consumed.
    pending_rows = (
        (
            await session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
                    IMConnectorAccount.platform == "wecom",  # type: ignore[arg-type]
                    IMConnectorAccount.external_account_id.like("pending_%"),  # type: ignore[attr-defined]
                )
            )
        )
        .scalars()
        .all()
    )
    pending = pending_rows[0] if pending_rows else None

    if pending is None:
        external_id = f"pending_{secrets.token_hex(8)}"
        secret_payload = json.dumps(
            {
                "bot_id": body.bot_id,
                "secret": body.secret,
                "bot_open_id": body.bot_id,
            }
        )
        try:
            credential_id = await svc._credentials.create(
                kind="im_bot",
                name=f"wecom:{external_id}",
                plaintext=secret_payload,
            )
        except Exception as exc:
            raise ValueError(f"failed to create credential: {exc}") from exc
        try:
            pending = IMConnectorAccount(
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                platform="wecom",
                external_account_id=external_id,
                acting_user_id=ctx.user.id,
                credential_id=credential_id,
                delivery_mode="gateway",
                enabled=False,
                config={"bot_app_name": (body.bot_name or "").strip()},
            )
            session.add(pending)
            await session.commit()
            await session.refresh(pending)
        except Exception:
            await session.rollback()
            try:
                await svc._credentials.delete(credential_id=credential_id)
            except Exception:
                pass
            raise
    else:
        # Reuse existing pending — update config in case bot_name changed.
        if pending.config is None:
            pending.config = {}
        if body.bot_name:
            pending.config["bot_app_name"] = body.bot_name.strip()
        await session.commit()

    # Generate / refresh binding code
    previous_code = await get_pending_binding_for_account(
        redis, key_prefix=key_prefix, account_id=pending.id
    )
    keep_previous = False
    if previous_code:
        from cubeplex.im.wecom.binding_state import get_pending_binding

        keep_previous = (
            await get_pending_binding(redis, key_prefix=key_prefix, code=previous_code)
            is not None
        )

    if keep_previous and previous_code:
        code = previous_code
    else:
        if previous_code:
            from cubeplex.im.wecom.binding_state import delete_binding_state

            await delete_binding_state(redis, key_prefix=key_prefix, code=previous_code)
        code = await create_binding_state(
            redis, key_prefix=key_prefix, account_id=pending.id
        )

    await store_pending_binding(redis, key_prefix=key_prefix, account_id=pending.id, code=code)
    logger.info("[IM ws] binding code={} for account={}", code, pending.id)

    return WeComConnectOut(
        code=code,
        instruction="在企业微信中打开该机器人并发送 /connect <code> 完成绑定",
        expires_in=110,
    )


@router.post(
    "/wecom/connect",
    status_code=status.HTTP_201_CREATED,
    response_model=WeComConnectOut,
)
async def connect_wecom_binding(
    workspace_id: str,
    body: ConnectWecomAccountIn,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> WeComConnectOut:
    """Create a pending WeCom account and return a binding code.

    The user must send ``/connect <code>`` from the WeCom client to
    activate the account.
    """
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")

    return await _connect_wecom_binding(body, request, ctx, session, backend)
