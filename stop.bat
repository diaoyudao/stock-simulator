@echo off
chcp 65001 >nul 2>&1
title StockSim - Stop

echo Stopping backend (port 8000) ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8000 "') do (
    taskkill /pid %%a /f >nul 2>&1
)

echo Stopping frontend (port 5173) ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":5173 "') do (
    taskkill /pid %%a /f >nul 2>&1
)

:: Fallback: kill by window title
taskkill /fi "WINDOWTITLE eq StockSim-Backend" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq StockSim-Frontend" /f >nul 2>&1

echo.
echo   Servers stopped.
timeout /t 3 >nul
