# Qzhuli（Q助理）平台插件

Hermes 的 Qzhuli 消息平台适配器，通过 imnut 协议连接 Q助理手机/电脑端。

## 代码位置与运行时拷贝（重要）

**仓库源码**：`plugins/qzhuli/`（本目录，含 `adapter.py` / `plugin.yaml` / `__init__.py`）

**运行时加载的拷贝**（网关加载的是这些，不是仓库文件）：
- `~/.hermes/plugins/qzhuli/` — default profile
- `~/.hermes/profiles/<name>/plugins/qzhuli/` — 每个命名 profile 各一份

> ⚠️ 修改 `adapter.py` 后必须同步全部拷贝并重启网关进程才生效。
> 一键完成：
> ```bash
> scripts/sync_qzhuli_plugin.sh --restart
> ```
> （只同步：`scripts/sync_qzhuli_plugin.sh`；`--restart` 会 kill 网关进程，
> 由外部监督器自动拉起，加载新代码。）

## 网关进程

实际运行 qzhuli 适配器与消息授权的是独立网关进程：

```
.venv/bin/python -m hermes_cli.main gateway run --external-supervisor
```

它由外部监督器托管（被 kill 后自动重启），**重启桌面端不会重启它**。

## 授权模型

扫码绑定只打通传输通道；每条私信还要过网关准入闸门
（`gateway/run_inbound.py` → `gateway/authz_mixin.py`）。本适配器在以下时机
把绑定者 cid 自动写入配对存储（`PairingStore.approve_user`，幂等），实现
"扫码即连、无需人工批准"：

- `_bind_and_connect`：扫码绑定轮询成功、拿到 `sender_cid` 后
- `connect()` 已绑定分支：重启后凭据直连时同样自动批准

授权失败只记日志，不影响绑定与 WebSocket 连接。
