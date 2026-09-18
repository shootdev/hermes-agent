#!/usr/bin/env bash
# sync_qzhuli_plugin.sh — 把仓库 plugins/qzhuli 同步到所有运行时插件目录
#
# 背景：qzhuli 插件按 profile 各有一份独立拷贝，网关（gateway run --external-supervisor）
# 加载的是拷贝而不是仓库文件。改完仓库代码后必须同步，否则不生效。
# 同步位置：
#   ~/.hermes/plugins/qzhuli/                        （default profile）
#   ~/.hermes/profiles/<name>/plugins/qzhuli/        （各命名 profile）
#
# 用法：
#   scripts/sync_qzhuli_plugin.sh              # 只同步文件
#   scripts/sync_qzhuli_plugin.sh --restart    # 同步并重启网关（监督器会自动拉起）
#
# 注意：只同步源目录中存在的文件，不删除目标目录里的其他文件。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$REPO_ROOT/plugins/qzhuli"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

RESTART=0
for arg in "$@"; do
  case "$arg" in
    --restart) RESTART=1 ;;
    *) echo "未知参数: $arg（支持 --restart）" >&2; exit 2 ;;
  esac
done

sync_one() {
  local target="$1"
  if [ ! -d "$target" ]; then
    echo "skip: $target（不存在）"
    return
  fi
  # 源目录存在的文件逐个复制（保留目标中可能存在的私有文件）
  for f in "$SRC_DIR"/*; do
    case "$(basename "$f")" in
      README.md) continue ;;  # 纯文档，不进运行时目录
    esac
    cp "$f" "$target/"
  done
  rm -rf "$target/__pycache__"
  echo "✓ 已同步: $target"
}

echo "同步 qzhuli 插件 → 运行时目录（源: ${SRC_DIR}）"
sync_one "$HERMES_HOME/plugins/qzhuli"
for d in "$HERMES_HOME"/profiles/*/plugins/qzhuli; do
  [ -d "$d" ] && sync_one "$d"
done

if [ "$RESTART" = "1" ]; then
  echo
  echo "重启网关进程…"
  pkill -TERM -f "gateway run --external-supervisor" 2>/dev/null || true
  for i in $(seq 1 30); do
    sleep 2
    if pgrep -f "gateway run --external-supervisor" >/dev/null 2>&1; then
      echo "✓ 网关已由监督器拉起: $(pgrep -f 'gateway run --external-supervisor' | tr '\n' ' ')"
      exit 0
    fi
  done
  echo "⚠ 等待网关重启超时，请手动检查网关状态" >&2
  exit 1
fi

echo
echo "完成。已同步文件，还需重启网关进程才会生效："
echo "  scripts/sync_qzhuli_plugin.sh --restart"
