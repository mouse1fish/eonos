#!/bin/bash
# EonOS - AI + Desktop + Tools

echo "╔══════════════════════════════════════╗"
echo "║         EonOS v1.0                   ║"
echo "║   AI + Desktop + Tools Platform      ║"
echo "╚══════════════════════════════════════╝"
echo

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 not found. Please install Python 3.8+"
    exit 1
fi

# 安装可选依赖
echo "[INFO] Checking dependencies..."
pip3 install pyautogui Pillow aiohttp --quiet 2>/dev/null

# Linux额外依赖
if [[ "$OSTYPE" == "linux"* ]]; then
    if ! command -v xdotool &> /dev/null; then
        echo "[WARN] xdotool not found. Install: sudo apt install xdotool"
    fi
    if ! command -v scrot &> /dev/null; then
        echo "[WARN] scrot not found. Install: sudo apt install scrot"
    fi
fi

# 启动EonOS
echo "[INFO] Starting EonOS..."
echo "[INFO] Web UI: http://127.0.0.1:8000"
echo

python3 -m eonos "$@"
