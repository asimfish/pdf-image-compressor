#!/bin/bash
# PDF Manager — 启动/停止/状态管理脚本
# 用法: ./start.sh [start|stop|status|restart]

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$PROJECT_DIR/.server.pid"
LOG_FILE="$PROJECT_DIR/.server.log"
PORT=8765

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "服务已在运行 (PID: $(cat "$PID_FILE"))"
        echo "访问: http://127.0.0.1:$PORT"
        return 0
    fi

    echo "启动 PDF Manager..."
    cd "$PROJECT_DIR"
    nohup .venv/bin/python -c "
import sys; sys.path.insert(0, 'src')
from file_compressor.cli import main
main()
" -- web --port $PORT > "$LOG_FILE" 2>&1 &

    echo $! > "$PID_FILE"
    sleep 2

    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "启动成功! (PID: $(cat "$PID_FILE"))"
        echo "访问: http://127.0.0.1:$PORT"
    else
        echo "启动失败，查看日志: $LOG_FILE"
        rm -f "$PID_FILE"
        return 1
    fi
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "停止服务 (PID: $PID)..."
            kill "$PID"
            sleep 1
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID"
            fi
            echo "已停止"
        else
            echo "服务未运行"
        fi
        rm -f "$PID_FILE"
    else
        echo "服务未运行"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "运行中 (PID: $(cat "$PID_FILE"))"
        echo "访问: http://127.0.0.1:$PORT"
        curl -s "http://127.0.0.1:$PORT/api/stats" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "(无法获取统计信息)"
    else
        echo "未运行"
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    *)       echo "用法: $0 {start|stop|restart|status}" ;;
esac
