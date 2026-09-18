"""Qzhuli 适配器：绑定成功后自动把绑定者 cid 写入配对存储（扫码即授权）。

回归背景：扫码绑定只是打通传输通道；若不自动批准，该 cid 的私信会被网关
准入闸门（gateway/run_inbound.py _is_user_authorized_for_source）拦下，走
人工配对流程（日志表现为 "Unauthorized user: ... on qzhuli"）。
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry


@pytest.fixture(autouse=True)
def _qzhuli_registered():
    """qzhuli 是插件平台，需注册进 registry 后 Platform("qzhuli") 才可解析。"""
    platform_registry.register(
        PlatformEntry(
            name="qzhuli",
            label="Qzhuli",
            adapter_factory=lambda cfg: None,
            check_fn=lambda: True,
            allowed_users_env="QZHULI_ALLOWED_USERS",
            allow_all_env="QZHULI_ALLOW_ALL_USERS",
        )
    )
    yield
    platform_registry.unregister("qzhuli")


def _adapter(bind_key: str = "bot1-abc123"):
    from plugins.qzhuli.adapter import QzhuliAdapter

    config = PlatformConfig(enabled=True, extra={"environment": "release", "bind_key": bind_key})
    return QzhuliAdapter(config)


def _stub_bind_success(adapter):
    """让 _bind_and_connect 一轮轮询即成功，不真正连 WS / 不写 .env。"""
    async def _fake_poll():
        adapter.conv_id = "conv-1"
        adapter.sender_cid = "cid-123"
        adapter.ws_token = "tok-1"
        return True

    async def _fake_ws_loop():
        return None

    adapter._poll_bind_status = _fake_poll  # type: ignore[method-assign]
    adapter._persist_credentials = lambda: None  # type: ignore[method-assign]
    adapter._ws_loop = _fake_ws_loop  # type: ignore[method-assign]


def _run_bind(adapter) -> None:
    asyncio.run(adapter._bind_and_connect())


def test_bind_success_auto_approves_bound_cid():
    adapter = _adapter()
    _stub_bind_success(adapter)
    fake_store = MagicMock()

    with patch("gateway.pairing.PairingStore", return_value=fake_store):
        _run_bind(adapter)

    assert adapter.bind_status == "bound"
    fake_store.approve_user.assert_called_once_with(
        "qzhuli", "cid-123", user_name="Qzhuli 绑定用户"
    )


def test_auto_approve_failure_does_not_break_bind():
    """配对存储写失败只记日志，绑定与 WS 连接照常进行。"""
    adapter = _adapter()
    _stub_bind_success(adapter)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("pairing store unavailable")

    with patch("gateway.pairing.PairingStore", return_value=MagicMock(approve_user=_boom)):
        _run_bind(adapter)

    assert adapter.bind_status == "bound"


def test_no_cid_skips_auto_approve():
    adapter = _adapter()
    _stub_bind_success(adapter)

    async def _fake_poll_no_cid():
        adapter.conv_id = "conv-1"
        adapter.sender_cid = ""  # 服务端没回 cid，不授权
        adapter.ws_token = "tok-1"
        return True

    adapter._poll_bind_status = _fake_poll_no_cid  # type: ignore[method-assign]
    fake_store = MagicMock()

    with patch("gateway.pairing.PairingStore", return_value=fake_store):
        _run_bind(adapter)

    fake_store.approve_user.assert_not_called()


def test_connect_with_existing_credentials_auto_approves():
    """重启后凭据直连（不经过绑定轮询）也必须自动批准绑定者——
    否则已绑定的 bot 重启后仍卡在人工批准环节。"""
    adapter = _adapter()
    adapter.sender_cid = "cid-123"
    adapter.conv_id = "conv-1"
    adapter.ws_token = "tok-1"

    async def _fake_ws_loop():
        return None

    adapter._ws_loop = _fake_ws_loop  # type: ignore[method-assign]
    fake_store = MagicMock()

    with patch("gateway.pairing.PairingStore", return_value=fake_store):
        asyncio.run(adapter.connect())

    assert adapter.bind_status == "bound"
    fake_store.approve_user.assert_called_once_with(
        "qzhuli", "cid-123", user_name="Qzhuli 绑定用户"
    )


def test_connect_without_credentials_does_not_approve():
    """无凭据（仅有 bind_key）时走轮询分支，不产生授权副作用。"""
    adapter = _adapter()  # 有 bind_key 无凭据 → 轮询分支

    async def _noop_bind():
        return None

    adapter._bind_and_connect = _noop_bind  # type: ignore[method-assign]
    fake_store = MagicMock()

    with patch("gateway.pairing.PairingStore", return_value=fake_store):
        ok = asyncio.run(adapter.connect())

    assert ok is True
    assert adapter.bind_status == "pending"
    fake_store.approve_user.assert_not_called()
