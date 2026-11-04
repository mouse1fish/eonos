"""
EonOS ISO 打包脚本 v2
将 EonOS + MCP工具 + AI架构 + 兼容系统 打包为ISO文件
"""

import os
import shutil
import subprocess
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISO_DIR = os.path.join(ROOT_DIR, "eonos_iso_build")
ISO_OUTPUT = os.path.join(ROOT_DIR, "EonOS-v1.0.iso")

# 排除模式
EXCLUDE_COMMON = {"__pycache__", ".git", "node_modules", ".venv", "venv", "target"}
EXCLUDE_EXE = {"*.exe"}
EXCLUDE_BUILD = {"build", "dist", "*.spec", "*.pyc"}


def log(msg):
    print(f"[EonOS] {msg}", flush=True)


def safe_copytree(src, dst, ignore=None):
    """安全的copytree，带错误处理"""
    try:
        if os.path.exists(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=ignore, copy_function=shutil.copy2)
        return True
    except Exception as e:
        log(f"  ERROR copying {src}: {e}")
        return False


def make_ignore(extra_dirs=None, extra_exts=None):
    """创建ignore函数"""
    ignore_dirs = EXCLUDE_COMMON | (extra_dirs or set())
    ignore_exts = set() | (extra_exts or set())

    def _ignore(path, names):
        ignored = set()
        for name in names:
            if name in ignore_dirs:
                ignored.add(name)
            for ext in ignore_exts:
                if name.endswith(ext.replace("*", "")):
                    ignored.add(name)
        return ignored

    return _ignore


def step_copy(name, src, dst, ignore=None):
    """带进度输出的复制步骤"""
    log(f"  Copying {name}...")
    if not os.path.exists(src):
        log(f"  SKIP: {name} not found at {src}")
        return False
    result = safe_copytree(src, dst, ignore)
    if result:
        # 计算大小
        total = 0
        for dp, dn, fn in os.walk(dst):
            for f in fn:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        log(f"  OK: {name} ({total / 1024 / 1024:.1f} MB)")
    return result


def main():
    start = time.time()
    log("=" * 50)
    log("EonOS ISO Builder v2")
    log("=" * 50)

    # 1. 清理
    log("Step 1: Clean build dir")
    if os.path.exists(ISO_DIR):
        shutil.rmtree(ISO_DIR, ignore_errors=True)
    os.makedirs(ISO_DIR, exist_ok=True)

    # 2. 复制EonOS核心
    log("Step 2: Copy EonOS core")
    step_copy("eonos",
              os.path.join(ROOT_DIR, "eonos"),
              os.path.join(ISO_DIR, "eonos"),
              make_ignore())

    # 3. 复制AI架构
    log("Step 3: Copy AI architecture")
    step_copy("ai-architecture",
              os.path.join(ROOT_DIR, "trae--aidamoxing", "AI architecture"),
              os.path.join(ISO_DIR, "ai-architecture"),
              make_ignore(
                  extra_dirs={"target", "dist", "build", "gguf_models"},
                  extra_exts={"*.exe", "*.spec"}
              ))

    # 4. 复制MCP工具 - cursor
    log("Step 4: Copy MCP tools (cursor)")
    step_copy("mcp-cursor",
              os.path.join(ROOT_DIR, "cursor--------mcp"),
              os.path.join(ISO_DIR, "mcp-tools", "cursor-mcp"),
              make_ignore(
                  extra_dirs={
                      "target", "Cargo", "cargo",
                      "MetaGPT-main", "UI-TARS-desktop-main",
                      "RuView-main", "biomcp-main",
                      "bytebot-main", "investor-agent-main",
                      "droidmind-main", "ros-mcp-server-main",
                      "rtk-develop", "smart-tree-main",
                      "PiloTY-main", "agency-agents-main",
                      "CLI-Anything-main",
                  },
                  extra_exts={"*.exe", "*.dll"}
              ))

    # 5. 复制MCP工具 - trae
    log("Step 5: Copy MCP tools (trae)")
    step_copy("mcp-trae",
              os.path.join(ROOT_DIR, "trae--aidamoxing", "mcp"),
              os.path.join(ISO_DIR, "mcp-tools", "trae-mcp"),
              make_ignore(extra_dirs={"target", "Cargo", "cargo"},
                          extra_exts={"*.exe"}))

    # 6. 复制WinBoat
    log("Step 6: Copy WinBoat")
    step_copy("winboat",
              os.path.join(ROOT_DIR, "trea-liunx-windows", "winboat-0.9.0"),
              os.path.join(ISO_DIR, "winboat"),
              make_ignore(extra_dirs={"dist"}))

    # 7. 复制DS4
    log("Step 7: Copy DS4")
    step_copy("ds4",
              os.path.join(ROOT_DIR, "trae--aidamoxing", "ds4-main"),
              os.path.join(ISO_DIR, "ds4"))

    # 8. 复制Qwen模型 - 只复制gguf文件
    log("Step 8: Copy Qwen model")
    model_src = os.path.join(ROOT_DIR, "trae--aidamoxing", "Qwen3.5-2B")
    model_dst = os.path.join(ISO_DIR, "models", "Qwen3.5-2B")
    if os.path.exists(model_src):
        os.makedirs(model_dst, exist_ok=True)
        # 只复制gguf模型文件和engine源码
        for item in os.listdir(model_src):
            src_path = os.path.join(model_src, item)
            dst_path = os.path.join(model_dst, item)
            if item == "models" and os.path.isdir(src_path):
                # 复制模型文件目录
                os.makedirs(dst_path, exist_ok=True)
                for f in os.listdir(src_path):
                    if f.endswith(".gguf"):
                        log(f"  Copying model: {f}")
                        shutil.copy2(os.path.join(src_path, f), os.path.join(dst_path, f))
            elif item == "engine" and os.path.isdir(src_path):
                # 只复制源码，不复制构建产物
                step_copy("qwen-engine-src",
                          src_path, dst_path,
                          make_ignore(extra_dirs={"build", "build2", "build3"},
                                      extra_exts={"*.dll", "*.lib", "*.pdb", "*.zip"}))
            elif item.endswith((".txt", ".md", ".json")):
                shutil.copy2(src_path, dst_path)
        log("  OK: Qwen model files")
    else:
        log("  SKIP: Qwen model not found")

    # 9. 创建安装脚本
    log("Step 9: Create install scripts")
    _create_install_scripts()
    _create_readme()
    _create_autorun()

    # 10. 计算总大小
    total_size = 0
    for dp, dn, fn in os.walk(ISO_DIR):
        for f in fn:
            try:
                total_size += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    log(f"Total build size: {total_size / 1024 / 1024:.1f} MB")

    # 11. 生成ISO
    log("Step 10: Build ISO")
    _build_iso()

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s")
    if os.path.exists(ISO_OUTPUT):
        iso_size = os.path.getsize(ISO_OUTPUT) / (1024 * 1024)
        log(f"ISO: {ISO_OUTPUT} ({iso_size:.1f} MB)")
    else:
        log(f"ISO not created. Files in: {ISO_DIR}")
        log("You can install directly from the build directory.")


def _create_install_scripts():
    """创建安装脚本"""
    # Windows
    with open(os.path.join(ISO_DIR, "install.bat"), "w", encoding="utf-8") as f:
        f.write(r"""@echo off
chcp 65001 >nul
title EonOS Installer
echo ==========================================
echo   EonOS v1.0 - AI + Desktop + Tools
echo ==========================================
echo.
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.8+ required. Install from https://python.org
    pause & exit /b 1
)
set INSTALL_DIR=%LOCALAPPDATA%\EonOS
set /p INSTALL_DIR="Install to [%INSTALL_DIR%]: "
echo Installing...
xcopy /E /I /Y eonos "%INSTALL_DIR%\eonos" >nul 2>&1
xcopy /E /I /Y ai-architecture "%INSTALL_DIR%\ai-architecture" >nul 2>&1
xcopy /E /I /Y mcp-tools "%INSTALL_DIR%\mcp-tools" >nul 2>&1
if exist models xcopy /E /I /Y models "%INSTALL_DIR%\models" >nul 2>&1
if exist winboat xcopy /E /I /Y winboat "%INSTALL_DIR%\winboat" >nul 2>&1
if exist ds4 xcopy /E /I /Y ds4 "%INSTALL_DIR%\ds4" >nul 2>&1
pip install pyautogui Pillow aiohttp --quiet 2>nul
echo.
echo Done! Run: %INSTALL_DIR%\eonos\start.bat
echo Web: http://127.0.0.1:8000
pause
""")
    # Linux
    with open(os.path.join(ISO_DIR, "install.sh"), "w", encoding="utf-8") as f:
        f.write(r"""#!/bin/bash
echo "=========================================="
echo "  EonOS v1.0 - AI + Desktop + Tools"
echo "=========================================="
D="${HOME}/EonOS"
read -p "Install to [$D]: " i; D="${i:-$D}"
cp -r eonos "$D/eonos"
cp -r ai-architecture "$D/ai-architecture"
cp -r mcp-tools "$D/mcp-tools"
[ -d models ] && cp -r models "$D/models"
[ -d winboat ] && cp -r winboat "$D/winboat"
[ -d ds4 ] && cp -r ds4 "$D/ds4"
pip3 install pyautogui Pillow aiohttp --quiet 2>/dev/null
echo "Done! Run: $D/eonos/start.sh"
""")
    log("  Created install scripts")


def _create_readme():
    with open(os.path.join(ISO_DIR, "README.txt"), "w", encoding="utf-8") as f:
        f.write("""EonOS v1.0 - AI + Desktop + Tools
==================================
Windows: install.bat -> eonos/start.bat
Linux:   bash install.sh -> bash eonos/start.sh
Web UI:  http://127.0.0.1:8000
""")


def _create_autorun():
    with open(os.path.join(ISO_DIR, "autorun.inf"), "w") as f:
        f.write("[autorun]\nopen=install.bat\nlabel=EonOS v1.0\n")


def _build_iso():
    """生成ISO"""
    # 尝试系统工具
    for tool in ["oscdimg", "mkisofs", "genisoimage"]:
        result = subprocess.run(f"where {tool}", shell=True, capture_output=True)
        if result.returncode == 0:
            log(f"  Using {tool}")
            if tool == "oscdimg":
                cmd = f'oscdimg -m -u -l"EonOS v1.0" "{ISO_DIR}" "{ISO_OUTPUT}"'
            elif tool == "mkisofs":
                cmd = f'mkisofs -o "{ISO_OUTPUT}" -V "EonOS v1.0" -J -R "{ISO_DIR}"'
            else:
                cmd = f'genisoimage -o "{ISO_OUTPUT}" -V "EonOS v1.0" -J -R "{ISO_DIR}"'
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if r.returncode == 0:
                log(f"  ISO created: {ISO_OUTPUT}")
                return True
            log(f"  {tool} failed: {r.stderr[:200]}")

    # Python fallback
    log("  No system ISO tool, trying pycdlib...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "pycdlib", "--quiet"],
                       capture_output=True)
        import pycdlib
        iso = pycdlib.PyCdlib()
        iso.new(vol_ident="EonOS v1.0", joliet=3, rock_ridge="1.09")
        count = 0
        for root, dirs, files in os.walk(ISO_DIR):
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, ISO_DIR).replace(os.sep, "/")
                iso_path = f"/{rel}"
                try:
                    iso.add_file(full, iso_path, joliet_path=iso_path)
                    count += 1
                except pycdlib.pycdlibexception.PyCdlibInvalidInput:
                    pass
                except Exception:
                    pass
        log(f"  Added {count} files to ISO")
        iso.write(ISO_OUTPUT)
        iso.close()
        log(f"  ISO created: {ISO_OUTPUT}")
        return True
    except Exception as e:
        log(f"  pycdlib failed: {e}")

    log("  ISO build failed. You can use the build directory directly.")
    return False


if __name__ == "__main__":
    main()
