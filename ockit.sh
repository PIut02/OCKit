#!/usr/bin/env sh
# ockit launcher (macOS / Linux) - find a usable Python 3 interpreter
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/ockit.py" "$@"
fi

if command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(0 if sys.version_info[0]>=3 else 1)' 2>/dev/null; then
    exec python "$SCRIPT_DIR/ockit.py" "$@"
fi

echo "[ockit] 未找到 python3。"
if [ "$(uname)" = "Darwin" ]; then
    echo "macOS: 直接再运行一次本脚本，系统会引导安装命令行开发者工具（含 python3）。"
    echo "       或手动安装: xcode-select --install"
else
    echo "Linux: 请用发行版包管理器安装，例如:"
    echo "       sudo apt install python3   /   sudo dnf install python3"
fi
exit 1
