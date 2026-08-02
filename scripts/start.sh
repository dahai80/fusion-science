#!/usr/bin/env bash
# =============================================================================
# fusion-science 启动脚本
# 用法:
#   ./scripts/start.sh                     # 前台启动
#   ./scripts/start.sh --daemon            # 后台启动
#   ./scripts/start.sh --mode science      # 指定模式
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 加载环境变量
if [ -f "$PROJECT_DIR/config/.env" ]; then
    set -a
    source "$PROJECT_DIR/config/.env"
    set +a
fi

# 默认参数
MODE="${MODE:-science}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-11434}"
DAEMON=false

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --daemon|-d) DAEMON=true; shift ;;
        --mode|-m) MODE="$2"; shift 2 ;;
        --host) HOST="$2"; shift 2 ;;
        --port|-p) PORT="$2"; shift 2 ;;
        --help|-h)
            echo "用法: $0 [选项]"
            echo "  --daemon, -d     后台运行"
            echo "  --mode, -m MODE  运行模式 (science|agent|cli)"
            echo "  --host HOST      绑定地址 (默认 127.0.0.1)"
            echo "  --port, -p PORT  监听端口 (默认 11434)"
            exit 0
            ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

# 激活虚拟环境
VENV_DIR="$PROJECT_DIR/.venv"
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
fi

export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"

# 启动
CMD="fusion-science run"
if [ "$MODE" = "science" ]; then
    CMD="fusion-science run"
elif [ "$MODE" = "cli" ]; then
    CMD="fusion-science"
fi

echo "🚀 启动 Fusion-Science (mode=$MODE)"
echo "   Host: $HOST:$PORT"
echo "   Mode: $MODE"
echo "   Offline: ${FUSION_OFFLINE_MODE:-true}"
echo ""

if [ "$DAEMON" = true ]; then
    nohup $CMD > "$PROJECT_DIR/fusion-science.log" 2>&1 &
    PID=$!
    echo "✅ 已后台启动, PID=$PID"
    echo "   日志: $PROJECT_DIR/fusion-science.log"
    echo "   停止: kill $PID"
else
    exec $CMD
fi