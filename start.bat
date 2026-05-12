@echo off
title StockSim - Start

echo ========================================
echo   StockSimulator - Quick Start
echo ========================================
echo.

:: ---- 启动后端 ----
echo [1/2] Starting backend (FastAPI :8000) ...
start "StockSim-Backend" cmd /k "cd /d %~dp0backend && .venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000"

:: 等后端初始化
timeout /t 4 /nobreak >nul

:: ---- 启动前端 ----
echo [2/2] Starting frontend (Vite :5173) ...
start "StockSim-Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:5173
echo.
echo   Close this window will NOT stop servers.
echo   Close the Backend/Frontend cmd windows to stop.
echo.
pause
