#!/bin/bash
# fusion-science lifecycle manager (start|stop|restart|status)
# HTTP REST API on port 11462 (health endpoint: /api/v1/health).
# Callers: fusion-studio UpstreamServiceManager (auto-start on launch + manual start).
# Affected API: start.sh start|stop|restart|status; status exits 0 if running, 1 if not.
# Data schemas: PID file .fusion-science.pid; logs/stdout.log + logs/stderr.log.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="/Users/dahai/fusion/.venv"
PID_FILE="${SCRIPT_DIR}/.fusion-science.pid"
LOG_DIR="${SCRIPT_DIR}/logs"
STDOUT_LOG="${LOG_DIR}/stdout.log"
STDERR_LOG="${LOG_DIR}/stderr.log"
PORT="${FUSION_SCIENCE_PORT:-11462}"
HOST="${FUSION_SCIENCE_HOST:-127.0.0.1}"
WORKERS="${FUSION_SCIENCE_WORKERS:-1}"
HEALTH_URL="http://${HOST}:${PORT}/api/v1/health"
HEALTH_WAIT=60

log_info()  { printf "\033[0;32m[INFO]\033[0m  %s\n" "$*"; }
log_warn()  { printf "\033[0;33m[WARN]\033[0m  %s\n" "$*"; }
log_error() { printf "\033[0;31m[ERROR]\033[0m %s\n" "$*"; }

ensure_venv() {
    if [[ -f "${VENV}/bin/activate" ]]; then
        # shellcheck disable=SC1091
        source "${VENV}/bin/activate"
    else
        log_warn "no .venv found at ${VENV}, using system python3"
    fi
}

get_pid() {
    [[ -f "$PID_FILE" ]] && cat "$PID_FILE" || echo ""
}

is_running() {
    local pid
    pid=$(get_pid)
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

is_healthy() {
    is_running && curl -sf "$HEALTH_URL" >/dev/null 2>&1
}

start() {
    if is_running; then
        log_info "fusion-science already running (PID $(get_pid))"
        exit 0
    fi
    mkdir -p "$LOG_DIR"
    ensure_venv

    export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"
    export FUSION_SCIENCE_API_PORT="$PORT"

    if [[ -z "${FUSION_SCIENCE_ENGINE_API_KEY:-}" ]]; then
        local mlx_settings="${HOME}/.fusion-mlx/settings.json"
        if [[ -f "$mlx_settings" ]]; then
            local mlx_key
            mlx_key=$(python3 -c "import json; print(json.load(open('${mlx_settings}')).get('auth',{}).get('api_key',''))" 2>/dev/null || echo "")
            if [[ -n "$mlx_key" ]]; then
                export FUSION_SCIENCE_ENGINE_API_KEY="$mlx_key"
            fi
        fi
    fi

    if [[ -z "${FUSION_SCIENCE_MODEL_NAME:-}" ]]; then
        local mlx_key="${FUSION_SCIENCE_ENGINE_API_KEY:-}"
        local status_json
        status_json=$(curl -sf -H "X-Fusion-Route: fusion-science" \
            ${mlx_key:+-H "Authorization: Bearer ${mlx_key}"} \
            "http://localhost:11434/api/status" 2>/dev/null || echo "")
        if [[ -n "$status_json" ]]; then
            local loaded_model
            loaded_model=$(echo "$status_json" | python3 -c "import sys,json; d=json.load(sys.stdin); m=d.get('loaded_models',[]); print(m[0] if m else '')" 2>/dev/null || echo "")
            if [[ -n "$loaded_model" ]]; then
                export FUSION_SCIENCE_MODEL_NAME="$loaded_model"
                log_info "auto-detected MLX model: ${loaded_model}"
            fi
        fi
    fi

    log_info "starting fusion-science daemon (port=${PORT}, workers=${WORKERS})..."
    nohup python3 -m uvicorn fusion_science.api.app:app \
        --host "$HOST" --port "$PORT" --workers "$WORKERS" \
        >> "$STDOUT_LOG" 2>> "$STDERR_LOG" &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    log_info "launched (PID ${pid}), waiting for health endpoint..."

    local i
    for i in $(seq 1 "$HEALTH_WAIT"); do
        if is_healthy; then
            log_info "fusion-science running (PID ${pid}), health OK"
            exit 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            log_error "process exited prematurely. recent stderr:"
            tail -n 20 "$STDERR_LOG" 2>/dev/null || true
            rm -f "$PID_FILE"
            exit 1
        fi
        sleep 1
    done

    log_error "timeout after ${HEALTH_WAIT}s waiting for health. recent stderr:"
    tail -n 20 "$STDERR_LOG" 2>/dev/null || true
    exit 1
}

stop() {
    local pid
    pid=$(get_pid)
    if [[ -z "$pid" ]]; then
        log_info "fusion-science not running"
        return 0
    fi
    log_info "stopping fusion-science (PID ${pid})..."
    # SIGTERM lets the FastAPI lifespan run graceful shutdown (session DB
    # backup, connector close). Wait up to 15s for a clean exit.
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 30); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
    done
    # F-O9: only escalate to SIGKILL if SIGTERM did not land, then verify the
    # process is actually gone so a zombie/reparented PID is not reported as
    # "stopped" while still holding the port.
    if kill -0 "$pid" 2>/dev/null; then
        log_info "graceful stop timed out, sending SIGKILL (PID ${pid})"
        kill -9 "$pid" 2>/dev/null || true
        sleep 1
    fi
    if kill -0 "$pid" 2>/dev/null; then
        log_info "WARNING: PID ${pid} still alive after SIGKILL — may be reparented or zombie"
    else
        log_info "stopped"
    fi
    rm -f "$PID_FILE"
}

status() {
    if is_healthy; then
        echo "running (PID $(get_pid), port=${PORT})"
        exit 0
    fi
    echo "not running"
    exit 1
}

restart() {
    stop || true
    start
}

case "${1:-status}" in
    start)   start ;;
    stop)    stop ;;
    restart) restart ;;
    status)  status ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 2
        ;;
esac
