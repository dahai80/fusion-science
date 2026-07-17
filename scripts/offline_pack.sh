#!/usr/bin/env bash
# =============================================================================
# fusion-science 离线依赖打包脚本
# 用于在无网络 Mac 上部署，需在有网络的环境先执行此脚本
# 用法: bash scripts/offline_pack.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="$PROJECT_DIR/offline_pkgs"
REQUIREMENTS="$PROJECT_DIR/requirements.txt"

echo "📦 Fusion-Science 离线依赖打包"
echo "==============================="

# 激活虚拟环境
VENV_DIR="$PROJECT_DIR/.venv"
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
fi

# 生成 requirements.txt
echo "生成 requirements.txt..."
pip freeze > "$REQUIREMENTS"
wc -l "$REQUIREMENTS"

# 下载所有包
echo "下载离线包到 $OUTPUT_DIR ..."
mkdir -p "$OUTPUT_DIR"
pip download -d "$OUTPUT_DIR" -r "$REQUIREMENTS" 2>&1 | tail -5

echo ""
echo "==============================="
echo "✅ 离线包已下载: $OUTPUT_DIR"
echo "   包数量: $(ls "$OUTPUT_DIR"/*.whl 2>/dev/null | wc -l | tr -d ' ')"
echo ""
echo "在断网 Mac 上执行:"
echo "  pip install --no-index --find-links=./offline_pkgs -r requirements.txt"
echo "==============================="