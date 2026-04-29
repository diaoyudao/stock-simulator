#!/bin/bash
# StockSimulator 环境启动脚本

set -e

echo "=== StockSimulator 环境检查 ==="

# 1. 检查 Python
if command -v python &> /dev/null; then
    PYTHON=python
elif command -v python3 &> /dev/null; then
    PYTHON=python3
else
    echo "ERROR: Python not found"
    exit 1
fi
echo "Python: $($PYTHON --version)"

# 2. 检查 Node.js
if command -v node &> /dev/null; then
    echo "Node: $(node --version)"
else
    echo "WARNING: Node.js not found, frontend will not work"
fi

# 3. 检查后端依赖
if [ -d "backend" ]; then
    cd backend
    if [ ! -d ".venv" ]; then
        echo "Creating Python virtual environment..."
        $PYTHON -m venv .venv
    fi
    source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null
    pip install -r requirements.txt -q
    echo "Backend dependencies: OK"
    cd ..
fi

# 4. 检查前端依赖
if [ -d "frontend" ]; then
    cd frontend
    if [ ! -d "node_modules" ]; then
        echo "Installing frontend dependencies..."
        npm install
    fi
    echo "Frontend dependencies: OK"
    cd ..
fi

# 5. 健康检查
if [ -f "backend/main.py" ]; then
    echo "Backend entry: OK"
fi

echo "=== 环境检查完成 ==="
