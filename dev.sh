#!/bin/bash
# StockSimulator 开发服务器启动/停止脚本
# 用法: bash dev.sh [start|stop|restart]

ACTION="${1:-start}"
BACKEND_PORT=8000
FRONTEND_PORT=5173

kill_port() {
    local port=$1
    # Windows: 通过PowerShell查找并杀掉监听该端口的所有进程
    pids=$(powershell -Command "Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess" 2>/dev/null)
    if [ -n "$pids" ]; then
        for pid in $pids; do
            powershell -Command "Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue" 2>/dev/null
        done
        sleep 2
        # 确认端口释放
        remaining=$(powershell -Command "(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count" 2>/dev/null)
        if [ "$remaining" != "0" ] && [ -n "$remaining" ]; then
            echo "WARNING: Port $port still in use, killing all python/node processes"
            powershell -Command "Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force" 2>/dev/null
            powershell -Command "Get-Process -Name node -ErrorAction SilentlyContinue | Stop-Process -Force" 2>/dev/null
            sleep 2
        fi
    fi
}

wait_port() {
    local port=$1
    local max_wait=$2
    local elapsed=0
    while [ $elapsed -lt $max_wait ]; do
        if curl -s "http://127.0.0.1:$port" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

do_stop() {
    echo "Stopping servers..."
    kill_port $BACKEND_PORT
    kill_port $FRONTEND_PORT
    echo "Stopped."
}

do_start() {
    # 先确保端口空闲
    kill_port $BACKEND_PORT
    kill_port $FRONTEND_PORT

    # 启动后端
    echo "Starting backend on :$BACKEND_PORT ..."
    cd backend
    source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null
    python -m uvicorn main:app --reload --port $BACKEND_PORT &
    BACKEND_PID=$!
    cd ..

    # 启动前端
    echo "Starting frontend on :$FRONTEND_PORT ..."
    cd frontend
    npm run dev &
    FRONTEND_PID=$!
    cd ..

    # 等待后端就绪
    if wait_port $BACKEND_PORT 10; then
        echo "Backend ready at http://localhost:$BACKEND_PORT"
    else
        echo "WARNING: Backend not responding after 10s"
    fi

    # 等待前端就绪
    if wait_port $FRONTEND_PORT 10; then
        echo "Frontend ready at http://localhost:$FRONTEND_PORT"
    else
        echo "WARNING: Frontend not responding after 10s"
    fi

    echo ""
    echo "PIDs: backend=$BACKEND_PID frontend=$FRONTEND_PID"
    echo "Use 'bash dev.sh stop' to stop servers"
}

case $ACTION in
    stop)    do_stop ;;
    restart) do_stop; do_start ;;
    start|*) do_start ;;
esac
