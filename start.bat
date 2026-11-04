@echo off
chcp 65001 >nul
title EonOS - AI + Desktop + Tools

echo ╔══════════════════════════════════════╗
echo ║         EonOS v1.0                   ║
echo ║   AI + Desktop + Tools Platform      ║
echo ╚══════════════════════════════════════╝
echo.

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

:: 安装可选依赖
echo [INFO] Checking dependencies...
pip install pyautogui Pillow aiohttp --quiet 2>nul

:: 启动EonOS
echo [INFO] Starting EonOS...
echo [INFO] Web UI: http://127.0.0.1:8000
echo.
python -m eonos %*

pause
