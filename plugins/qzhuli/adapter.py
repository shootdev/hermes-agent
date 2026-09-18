"""Qzhuli (Q助理) platform adapter for Hermes Agent.

hermes-dev 第 2 层产物（插件，位于 ~/.hermes/plugins/qzhuli/），不修改仓库核心。

协议依据（全部实测确认）：
- 绑定：Q助理 doc/aimachine.md + shootdev/LobsterAI 的 IMSettings.tsx 扫码流程。
  二维码内容 = JSON {"type":"imnut_bind","key":"<bind_key>","id":2}，用户手机 Q助理 App 扫码确认后
  Q助理调用 imnut 的 /api/v1/lobster/bind/webhook 创建会话；Hermes 轮询
  GET https://<client-host>/aimachine/check_bind_status?bind_key=... 直至 status=1，
  获得 conversation_id / cid / bind_token。
- 收消息：WebSocket wss://<im-host>/wss_openclaw?cid=<cid>&token=<bind_token>&conv_id=<conv_id>
  （服务端配置 OpenClawWSToken 时需 Sec-WebSocket-Protocol: X-OpenClaw-Token.<value>），
  消息帧 {"type":"inbound_message","payload":{msg_id,conv_id,sender_cid,content,msg_type,ts}}。
- 发消息：POST https://<im-host>/api/v1/conversations/push_message
  body {conv_id, content, msg_type:1, role:0/1, sender_cid}（msg_type=1 为 LobsterAI 实际值）。
- 心跳：{"type":"ping","ts":...} → {"type":"pong"}；重连指数退避 2s→30s。
"""

import asyncio
import datetime
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from websockets.asyncio.client import connect as ws_connect

from gateway.platforms._shared import (
    get_scoped_secret as _get_scoped_secret,
    seed_extra_from_env as _seed_extra_from_env,
    send_error,
)
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.config import Platform
from gateway.platforms.event import MessageEvent, MessageType

logger = logging.getLogger(__name__)

# hermes-dev: Qzhuli 域名表（release 生产 / dev 测试）
_QZHULI_CLIENT_HOST = {"release": "client.qzhuli.com", "dev": "test.client.qzhuli.com"}
_QZHULI_IM_HOST = {"release": "im.qzhuli.com", "dev": "test.im.qzhuli.com"}
# WS 专用 host（默认与 IM host 相同；测试可分离端口）
_QZHULI_WS_HOST: Dict[str, str] = {}
# URL scheme（生产 HTTPS/WSS；测试可 monkeypatch 为 http/ws）
_SCHEME = "https"
_WS_SCHEME = "wss"
_CHECK_BIND_PATH = "/aimachine/check_bind_status"
_WS_PATH = "/wss_openclaw"
_PUSH_PATH = "/api/v1/conversations/push_message"
_BIND_POLL_INTERVAL_S = 5
_BIND_POLL_TIMEOUT_S = 600  # 10 分钟未扫码确认则放弃
_PUSH_TIMEOUT_S = 30
_RECONNECT_MIN_S = 2
_RECONNECT_MAX_S = 30
_HEARTBEAT_INTERVAL_S = 30

_TRUTHY = {"1", "true", "yes"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _resolve_env(config) -> str:
    extra = getattr(config, "extra", {}) or {}
    raw = (_get_scoped_secret("QZHULI_ENVIRONMENT") or str(extra.get("environment") or "release")).strip().lower()
    return raw if raw in _QZHULI_CLIENT_HOST else "release"


class QzhuliAdapter(BasePlatformAdapter):
    """Async adapter bridging Qzhuli mobile conversations and Hermes via imnut."""

    def __init__(self, config, **kwargs):
        super().__init__(config=config, platform=Platform("qzhuli"))
        extra = getattr(config, "extra", {}) or {}
        self.environment = _resolve_env(config)
        self.bind_key = str(extra.get("bind_key") or "").strip()
        self.sender_cid = str(extra.get("sender_cid") or "").strip()
        self.conv_id = str(extra.get("conv_id") or "").strip()
        self.ws_token = str(extra.get("ws_token") or "").strip()
        # 绑定状态（供 desktop 轮询展示）
        self.bind_status: str = "bound" if (self.sender_cid and self.conv_id and self.ws_token) else "unbound"
        self._ws = None
        self._ws_task: Optional[asyncio.Task] = None
        self._bind_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._closed = False
        self._reconnect_delay = _RECONNECT_MIN_S

    @property
    def name(self) -> str:
        return "Qzhuli"

    def _fail(self, code: str, message: str, *, retryable: bool) -> bool:
        self._set_fatal_error(code, message, retryable=retryable)
        return False

    # ── 生命周期 ────────────────────────────────────────────────────────────

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        """已绑定 → 连 WS；仅持有 bind_key → 后台轮询绑定，不阻塞 connect。"""
        if self.environment not in _QZHULI_CLIENT_HOST:
            return self._fail("config_missing", f"unknown Qzhuli environment: {self.environment}", retryable=False)
        self._closed = False
        if self.sender_cid and self.conv_id and self.ws_token:
            self.bind_status = "bound"
            # 已绑定（重启后凭据直连）同样自动批准绑定者，否则旧绑定永远走不到
            # _bind_and_connect 的授权路径（幂等，重复调用无副作用）。
            self._auto_approve_bound_user()
            self._ws_task = asyncio.create_task(self._ws_loop())
            return True
        if self.bind_key:
            self.bind_status = "pending"
            self._bind_task = asyncio.create_task(self._bind_and_connect())
            logger.info("Qzhuli: no credentials yet — polling bind status with key=%s…", self.bind_key)
            return True
        return self._fail(
            "config_missing",
            "Qzhuli 未绑定：请在桌面端「消息平台 → Qzhuli」用二维码扫码绑定（或配置 QZHULI_SENDER_CID / "
            "QZHULI_CONV_ID / QZHULI_WS_TOKEN）",
            retryable=False,
        )

    async def disconnect(self) -> None:
        self._closed = True
        for task in (self._bind_task, self._ws_task, self._heartbeat_task):
            if task and not task.done():
                task.cancel()
        for task in (self._bind_task, self._ws_task, self._heartbeat_task):
            if task:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        self._ws = None
        self._mark_disconnected()

    # ── 绑定 ────────────────────────────────────────────────────────────────

    async def _bind_and_connect(self) -> None:
        """后台轮询 check_bind_status；绑定成功后持久化凭据并连接 WS。"""
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _BIND_POLL_TIMEOUT_S
            while not self._closed:
                if loop.time() > deadline:
                    self.bind_status = "timeout"
                    self._set_fatal_error("bind_timeout", "Qzhuli 绑定超时（10 分钟内未在手机端确认）", retryable=True)
                    await self._notify_fatal_error()
                    return
                try:
                    if await self._poll_bind_status():
                        self.bind_status = "bound"
                        self._persist_credentials()
                        self._auto_approve_bound_user()
                        logger.info("Qzhuli: bound conv=%s cid=%s — connecting websocket", self.conv_id, self.sender_cid)
                        self._ws_task = asyncio.create_task(self._ws_loop())
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # 网络抖动继续轮询
                    logger.warning("Qzhuli: bind poll error: %s", exc)
                await asyncio.sleep(_BIND_POLL_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Qzhuli: bind task failed: %s", exc)
            self._set_fatal_error("bind_error", str(exc), retryable=True)

    async def _poll_bind_status(self) -> bool:
        """一次 check_bind_status 轮询；绑定成功填充 conv_id/sender_cid/ws_token 并返回 True。"""
        host = _QZHULI_CLIENT_HOST[self.environment]
        url = f"{_SCHEME}://{host}{_CHECK_BIND_PATH}?bind_key={self.bind_key}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            body = resp.json()
        payload = body.get("data") if isinstance(body, dict) else None
        payload = payload if isinstance(payload, dict) else body
        if str(payload.get("status") or "").lower() not in ("1", "bound"):
            return False
        conv_id = str(payload.get("conversation_id") or payload.get("conv_id") or "").strip()
        sender_cid = str(payload.get("cid") or "").strip()
        token = str(payload.get("bind_token") or payload.get("token") or "").strip()
        if not (conv_id and sender_cid and token):
            logger.warning("Qzhuli: bind status says bound but fields incomplete: %s", payload)
            return False
        self.conv_id, self.sender_cid, self.ws_token = conv_id, sender_cid, token
        return True

    def _persist_credentials(self) -> None:
        """把绑定凭据写入 profile 的 .env，重启后无需重新扫码。"""
        try:
            from hermes_constants import get_hermes_home
            env_path = get_hermes_home() / ".env"
        except Exception:
            env_path = Path.home() / ".hermes" / ".env"
        updates = {
            "QZHULI_ENVIRONMENT": self.environment,
            "QZHULI_SENDER_CID": self.sender_cid,
            "QZHULI_CONV_ID": self.conv_id,
            "QZHULI_WS_TOKEN": self.ws_token,
        }
        keys = set(updates)
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        kept = [ln for ln in lines if not (ln.strip() and ln.split("=", 1)[0].strip() in keys and not ln.lstrip().startswith("#"))]
        for k, v in updates.items():
            kept.append(f"{k}={v}")
        env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        logger.info("Qzhuli: credentials persisted to %s", env_path)

    def _auto_approve_bound_user(self) -> None:
        """扫码绑定即授权：把绑定者 cid 直接写入当前 profile 的配对存储。

        扫码本身已由手机端 Q助理确认，等于一次强身份认证；若不自动批准，
        该 cid 的每条私信都会在网关准入闸门被拦下、进入人工批准环节。
        失败只记日志，不影响绑定与 WS 连接。
        """
        cid = self.sender_cid
        if not cid:
            return
        try:
            from gateway.pairing import PairingStore
            # 适配器运行在 profile serve 进程内，无参 PairingStore() 即落到
            # 该 profile 的 HERMES_HOME，与网关授权读取的是同一份存储。
            PairingStore().approve_user("qzhuli", cid, user_name="Qzhuli 绑定用户")
            logger.info("Qzhuli: auto-approved bound user cid=%s (pairing store)", cid)
        except Exception as exc:  # 授权失败不阻断绑定链路
            logger.warning("Qzhuli: auto-approve bound user failed (cid=%s): %s", cid, exc)

    # ── WebSocket ───────────────────────────────────────────────────────────

    def _ws_url(self) -> str:
        host = _QZHULI_WS_HOST.get(self.environment) or _QZHULI_IM_HOST[self.environment]
        import urllib.parse
        qs = urllib.parse.urlencode({
            "cid": self.sender_cid,
            "token": self.ws_token,
            "conv_id": self.conv_id,
        })
        return f"{_WS_SCHEME}://{host}{_WS_PATH}?{qs}"

    async def _ws_loop(self) -> None:
        """连接 wss_openclaw，循环收消息；断线指数退避重连。"""
        while not self._closed:
            try:
                protocols: Optional[list[str]] = None
                open_claw_token = _get_scoped_secret("QZHULI_OPENCLAW_TOKEN") or ""
                if open_claw_token.strip():
                    protocols = [f"X-OpenClaw-Token.{open_claw_token.strip()}"]
                kwargs: Dict[str, Any] = {"open_timeout": 15.0, "close_timeout": 5.0, "max_size": 4 * 1024 * 1024}
                if protocols:
                    kwargs["subprotocols"] = protocols
                async with ws_connect(self._ws_url(), **kwargs) as ws:
                    self._ws = ws
                    self._reconnect_delay = _RECONNECT_MIN_S
                    self._mark_connected()
                    logger.info("Qzhuli: websocket connected conv=%s", self.conv_id)
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    try:
                        async for raw in ws:
                            await self._handle_frame(raw)
                    finally:
                        self._cancel_safe(self._heartbeat_task)
                        self._heartbeat_task = None
                        self._ws = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Qzhuli: websocket error: %s", exc)
                self._set_fatal_error("ws_error", str(exc), retryable=True)
                self._mark_disconnected()
            if self._closed:
                break
            delay = self._reconnect_delay
            self._reconnect_delay = min(self._reconnect_delay * 2, _RECONNECT_MAX_S)
            logger.info("Qzhuli: reconnecting in %ss…", delay)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    @staticmethod
    def _cancel_safe(task: Optional[asyncio.Task]) -> None:
        if task and not task.done():
            task.cancel()

    async def _ws_send(self, text: str) -> None:
        if self._ws is not None:
            await self._ws.send(text)

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._closed and self._ws is not None:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
                if self._ws is None:
                    return
                try:
                    await self._ws_send(json.dumps({"type": "ping", "ts": _now_ms()}))
                except Exception:
                    return
        except asyncio.CancelledError:
            raise

    async def _handle_frame(self, raw: Any) -> None:
        """处理一帧 WS 消息：keepalive / subscribe / inbound_message。"""
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        text = text.strip()
        if not text:
            return
        if text.lower() == "ping":
            await self._ws_send("pong")
            return
        try:
            frame = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(frame, dict):
            return
        ftype = str(frame.get("type") or "").lower()
        if ftype in ("ping", "heartbeat"):
            await self._ws_send(json.dumps({"type": "pong", "ts": _now_ms()}))
            return
        if ftype != "inbound_message":
            return
        payload = frame.get("payload")
        if not isinstance(payload, dict):
            return
        conv_id = str(payload.get("conv_id") or "").strip()
        content = str(payload.get("content") or "").strip()
        sender_cid = str(payload.get("sender_cid") or "").strip()
        if not conv_id or not content:
            return
        # hermes-dev: 注意——inbound 的 sender_cid 就是绑定会话的用户 cid（与 self.sender_cid 相同），
        # 不能当作"自己的回显"过滤（LobsterAI qzhuliGateway.ts 亦无自过滤；imnut 不回推机器人 push 的消息）。
        logger.info(
            "Qzhuli: inbound msg=%s conv=%s sender=%s len=%d",
            str(payload.get("msg_id") or "?"), conv_id, sender_cid or "?", len(content),
        )
        msg_id = payload.get("msg_id")
        message_id = str(msg_id) if msg_id is not None else str(_now_ms())
        source = self.build_source(
            chat_id=conv_id,
            chat_name=conv_id,
            chat_type="dm",
            user_id=sender_cid or f"qzhuli:{conv_id}",
            user_name=sender_cid or "Qzhuli",
        )
        await self.handle_message(MessageEvent(
            text=content,
            message_type=MessageType.TEXT,
            source=source,
            message_id=message_id,
            timestamp=datetime.datetime.now(),
        ))

    # ── 发送 ────────────────────────────────────────────────────────────────

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        conv_id = (chat_id or self.conv_id or "").strip()
        if not conv_id:
            return SendResult(success=False, error="Qzhuli: no conversation id")
        try:
            await self._push_message(conv_id, content, role=0)
            return SendResult(success=True, message_id=str(_now_ms()))
        except Exception as exc:
            logger.warning("Qzhuli: send failed: %s", exc)
            return SendResult(success=False, error=str(exc))

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """Qzhuli 无 typing 指示，no-op。"""

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "dm"}

    async def _push_message(self, conv_id: str, text: str, role: int = 0) -> None:
        host = _QZHULI_IM_HOST[self.environment]
        url = f"{_SCHEME}://{host}{_PUSH_PATH}"
        payload: Dict[str, Any] = {"conv_id": conv_id, "content": text, "msg_type": 1, "role": role}
        if self.sender_cid:
            payload["sender_cid"] = self.sender_cid
        async with httpx.AsyncClient(timeout=_PUSH_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()
        code = body.get("code") if isinstance(body, dict) else None
        if isinstance(code, (int, float)) and code != 200:
            raise RuntimeError(f"Qzhuli push error: {body.get('msg') or code}")


# ── 插件注册 ────────────────────────────────────────────────────────────────

def check_requirements() -> bool:
    """deps 探针：httpx/websockets 均为 Hermes 自带依赖，恒 True。"""
    return True


def validate_config(config) -> bool:
    extra = getattr(config, "extra", {}) or {}
    return bool(extra.get("bind_key") or (extra.get("sender_cid") and extra.get("conv_id") and extra.get("ws_token")))


def is_connected(config) -> bool:
    extra = getattr(config, "extra", {}) or {}
    return bool(extra.get("sender_cid") and extra.get("conv_id") and extra.get("ws_token"))


def _env_enablement() -> Optional[dict]:
    """从 profile env seed adapter extra；完全未配置时返回 None（平台显示"需要设置"）。"""
    environment = (_get_scoped_secret("QZHULI_ENVIRONMENT") or "release").strip().lower()
    bind_key = (_get_scoped_secret("QZHULI_BIND_KEY") or "").strip()
    sender_cid = (_get_scoped_secret("QZHULI_SENDER_CID") or "").strip()
    conv_id = (_get_scoped_secret("QZHULI_CONV_ID") or "").strip()
    ws_token = (_get_scoped_secret("QZHULI_WS_TOKEN") or "").strip()
    if not (bind_key or sender_cid):
        return None
    seed = {"environment": environment}
    if bind_key:
        seed["bind_key"] = bind_key
    if sender_cid:
        seed["sender_cid"] = sender_cid
    if conv_id:
        seed["conv_id"] = conv_id
    if ws_token:
        seed["ws_token"] = ws_token
    return seed


def interactive_setup() -> None:
    """CLI 兜底设置：生成 bind_key 并打印二维码内容（desktop 面板是主入口）。"""
    from hermes_cli.setup import print_header, print_info, print_success, prompt, save_env_value
    print_header("Qzhuli")
    existing = _get_scoped_secret("QZHULI_BIND_KEY") or ""
    if not existing:
        existing = input("已有绑定密钥（留空自动生成）: ").strip() or ""
    if not existing:
        existing = __import__("uuid").uuid4().hex
    save_env_value("QZHULI_BIND_KEY", existing)
    env = prompt("环境（release/dev，默认 release）", default=_get_scoped_secret("QZHULI_ENVIRONMENT") or "release")
    save_env_value("QZHULI_ENVIRONMENT", env.strip() or "release")
    print_info("用手机 Q助理 App 扫描以下内容完成绑定：")
    print(json.dumps({"type": "imnut_bind", "key": existing, "id": 2}, ensure_ascii=False))
    print_success("绑定密钥已保存。重启 gateway 后 Hermes 会自动轮询绑定状态。")


def register(ctx):
    """Plugin entry point: called by the Hermes plugin system."""
    ctx.register_platform(
        name="qzhuli",
        label="Qzhuli",
        adapter_factory=QzhuliAdapter,
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["QZHULI_ENVIRONMENT", "QZHULI_BIND_KEY", "QZHULI_SENDER_CID", "QZHULI_CONV_ID", "QZHULI_WS_TOKEN"],
        install_hint="No extra packages needed (uses httpx + websockets already in Hermes)",
        setup_fn=interactive_setup,
        env_enablement_fn=_env_enablement,
        max_message_length=2000,
        emoji="🦞",
        allow_update_command=True,
        # hermes-dev: 准入 allowlist——网关默认拒绝无 allowlist 平台的未知发件人；
        # 声明这两个 env 名后，gateway 从 .env 读取 QZHULI_ALLOWED_USERS / QZHULI_ALLOW_ALL_USERS。
        allowed_users_env="QZHULI_ALLOWED_USERS",
        allow_all_env="QZHULI_ALLOW_ALL_USERS",
        platform_hint=(
            "You are chatting via Qzhuli (Q助理). The user's phone app relays your replies "
            "in real time. Keep responses concise and conversational; markdown is plain text "
            "in the mobile app."),
    )
