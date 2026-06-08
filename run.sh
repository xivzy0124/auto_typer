#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# ─── 检查 Python 3 ─────────────────────────────────────────────────────────────
# 优先使用 Homebrew 安装的 python3.13（兼容性最好）
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "[错误] 未找到 python3，请先安装 Python 3.9+"
    exit 1
fi

PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[信息] 使用 $PYTHON ($PY_VER)"

PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")
if [[ $PY_MINOR -lt 9 ]]; then
    echo "[错误] 需要 Python 3.9+，当前版本过低"
    exit 1
fi

# ─── 创建/激活虚拟环境 ─────────────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    echo "[安装] 创建虚拟环境..."
    $PYTHON -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# ─── 安装依赖 ─────────────────────────────────────────────────────────────────
NEED_INSTALL=0
python3 -c "import pynput" 2>/dev/null || NEED_INSTALL=1
python3 -c "import Quartz" 2>/dev/null || NEED_INSTALL=1

if [[ $NEED_INSTALL -eq 1 ]]; then
    echo "[安装] 安装依赖包（首次运行需要网络）..."
    pip install --quiet --upgrade pip
    pip install --quiet pynput pyobjc-framework-Quartz
    echo "[安装] 完成"
fi

# ─── 辅助功能权限提示 ─────────────────────────────────────────────────────────
echo ""
echo "=================================================="
echo "  Auto Typer — macOS 键盘自动输入工具"
echo "=================================================="
echo ""
echo "  [提示] 首次使用请授予辅助功能权限："
echo "    系统设置 → 隐私与安全性 → 辅助功能"
echo "    将「终端」或「iTerm」添加到允许列表"
echo ""
echo "  快捷键："
echo "    Ctrl+Option+1~9      输入对应槽位代码"
echo "    Ctrl+Option+0        暂停 / 恢复"
echo "    Ctrl+Option+-        重置（中断当前输入，可重新选择）"
echo "    Ctrl+C               退出"
echo "--------------------------------------------------"
echo ""

# ─── 启动主程序 ───────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
exec python3 auto_typer.py "$@"
