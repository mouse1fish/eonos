"""
EonOS 桌面操控 Agent
整合 WinBoat AI Agent 的屏幕感知和操控能力
AI鼠标操控可以替代大量简单MCP工具
"""

import asyncio
import base64
import io
import logging
import os
import platform
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ..core import EventBus, EventType, Event, bus
from ..core.command import executor

logger = logging.getLogger("eonos.agent")


class MouseAction(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    MOVE = "move"
    SCROLL_UP = "scroll_up"
    SCROLL_DOWN = "scroll_down"
    DRAG_START = "drag_start"
    DRAG_END = "drag_end"


class KeyAction(str, Enum):
    TYPE = "type"
    PRESS = "press"
    HOTKEY = "hotkey"


@dataclass
class ScreenshotResult:
    image_data: str  # base64
    width: int = 1920
    height: int = 1080
    timestamp: float = field(default_factory=time.time)
    ocr_text: Optional[str] = None


@dataclass
class ActionResult:
    success: bool
    action: str
    data: Any = None
    error: Optional[str] = None
    duration_ms: float = 0


class DesktopAgent:
    """
    桌面操控Agent - AI鼠标操控替代简单MCP工具

    可替代的MCP工具:
    - screenshot-mcp → agent.screenshot()
    - clipboard-mcp → agent.clipboard_copy/paste()
    - shortcut-mcp → agent.hotkey()
    - startup-mcp → agent.launch_app()
    - calculator-mcp → agent直接计算
    - color-mcp → agent.screenshot() + 取色
    - datetime-mcp → agent直接获取
    - password-mcp → agent生成
    - translate-mcp → agent直接翻译

    保留的MCP工具 (复杂操作不适合鼠标替代):
    - sqlite-mcp, git-mcp, docker-mcp, excel-mcp
    - code-runner-mcp, pdf-reader-mcp, csv-mcp
    - web-fetch-mcp, network-mcp, disk-mcp
    """

    def __init__(self, event_bus: EventBus = None):
        self.bus = event_bus or bus
        self._is_windows = platform.system() == "Windows"
        self._is_linux = platform.system() == "Linux"
        self._screen_size: Optional[Tuple[int, int]] = None
        self._last_screenshot: Optional[ScreenshotResult] = None
        self._safe_mode = True
        self._action_delay = 0.1

    async def screenshot(self, ocr: bool = True) -> ScreenshotResult:
        """截取屏幕"""
        start = time.time()
        try:
            if self._is_windows:
                result = await self._screenshot_windows(ocr)
            else:
                result = await self._screenshot_linux(ocr)

            self._last_screenshot = result

            await self.bus.emit_simple(
                EventType.AGENT_SCREENSHOT, "agent",
                {"width": result.width, "height": result.height,
                 "ocr": bool(result.ocr_text), "time_ms": (time.time() - start) * 1000}
            )
            return result
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            raise

    async def _screenshot_windows(self, ocr: bool) -> ScreenshotResult:
        """Windows截图"""
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            result = ScreenshotResult(
                image_data=img_b64,
                width=img.width, height=img.height
            )

            if ocr:
                result.ocr_text = await self._ocr_windows(img)

            return result
        except ImportError:
            # 回退到PowerShell截图
            cmd_result = await executor.execute_powershell(
                "Add-Type -AssemblyName System.Windows.Forms; "
                "Add-Type -AssemblyName System.Drawing; "
                "$s = [System.Windows.Forms.Screen]::PrimaryScreen; "
                "$b = New-Object System.Drawing.Bitmap($s.Bounds.Width, $s.Bounds.Height); "
                "$g = [System.Drawing.Graphics]::FromImage($b); "
                "$g.CopyFromScreen($s.Bounds.Location, [System.Drawing.Point]::Empty, $s.Bounds.Size); "
                "$ms = New-Object System.IO.MemoryStream; "
                "$b.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); "
                "[Convert]::ToBase64String($ms.ToArray())"
            )
            return ScreenshotResult(
                image_data=cmd_result.stdout.strip() if cmd_result.success else "",
                width=1920, height=1080
            )

    async def _screenshot_linux(self, ocr: bool) -> ScreenshotResult:
        """Linux截图"""
        result = await executor.execute("scrot -o /tmp/eonos_screenshot.png", timeout=5)
        if result.success:
            with open("/tmp/eonos_screenshot.png", "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            return ScreenshotResult(image_data=img_b64)
        return ScreenshotResult(image_data="")

    async def _ocr_windows(self, img) -> Optional[str]:
        """Windows OCR"""
        try:
            import pytesseract
            return pytesseract.image_to_string(img, lang="chi_sim+eng")
        except ImportError:
            return None

    # ==================== 鼠标操控 ====================

    async def click(self, x: int, y: int, button: str = "left",
                    clicks: int = 1) -> ActionResult:
        """鼠标点击"""
        return await self._mouse_action(MouseAction.CLICK, x, y, button=button, clicks=clicks)

    async def double_click(self, x: int, y: int) -> ActionResult:
        return await self._mouse_action(MouseAction.DOUBLE_CLICK, x, y)

    async def right_click(self, x: int, y: int) -> ActionResult:
        return await self._mouse_action(MouseAction.RIGHT_CLICK, x, y)

    async def move(self, x: int, y: int, duration: float = 0.3) -> ActionResult:
        return await self._mouse_action(MouseAction.MOVE, x, y, duration=duration)

    async def scroll(self, x: int, y: int, amount: int = 3) -> ActionResult:
        action = MouseAction.SCROLL_DOWN if amount > 0 else MouseAction.SCROLL_UP
        return await self._mouse_action(action, x, y, scroll_amount=abs(amount))

    async def drag(self, from_x: int, from_y: int,
                   to_x: int, to_y: int) -> ActionResult:
        await self._mouse_action(MouseAction.DRAG_START, from_x, from_y)
        await asyncio.sleep(0.1)
        return await self._mouse_action(MouseAction.DRAG_END, to_x, to_y)

    async def _mouse_action(self, action: MouseAction, x: int, y: int,
                            button: str = "left", clicks: int = 1,
                            duration: float = 0.3,
                            scroll_amount: int = 3) -> ActionResult:
        start = time.time()
        try:
            if self._is_windows:
                result = await self._mouse_windows(action, x, y, button, clicks, scroll_amount)
            else:
                result = await self._mouse_linux(action, x, y, button, clicks, scroll_amount)

            await self.bus.emit_simple(
                EventType.AGENT_MOUSE, "agent",
                {"action": action.value, "x": x, "y": y, "success": result.success}
            )

            if self._action_delay > 0:
                await asyncio.sleep(self._action_delay)

            return result
        except Exception as e:
            return ActionResult(success=False, action=action.value, error=str(e),
                               duration_ms=(time.time() - start) * 1000)

    async def _mouse_windows(self, action: MouseAction, x: int, y: int,
                             button: str, clicks: int,
                             scroll_amount: int) -> ActionResult:
        """Windows鼠标操控 - 使用pyautogui或PowerShell"""
        try:
            import pyautogui
            if action == MouseAction.CLICK:
                pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            elif action == MouseAction.DOUBLE_CLICK:
                pyautogui.doubleClick(x=x, y=y)
            elif action == MouseAction.RIGHT_CLICK:
                pyautogui.rightClick(x=x, y=y)
            elif action == MouseAction.MOVE:
                pyautogui.moveTo(x=x, y=y, duration=0.3)
            elif action == MouseAction.SCROLL_DOWN:
                pyautogui.scroll(-scroll_amount, x=x, y=y)
            elif action == MouseAction.SCROLL_UP:
                pyautogui.scroll(scroll_amount, x=x, y=y)
            elif action == MouseAction.DRAG_START:
                pyautogui.moveTo(x=x, y=y)
                pyautogui.mouseDown(button="left")
            elif action == MouseAction.DRAG_END:
                pyautogui.moveTo(x=x, y=y, duration=0.3)
                pyautogui.mouseUp(button="left")
            return ActionResult(success=True, action=action.value)
        except ImportError:
            # 回退到PowerShell
            return await self._mouse_powershell(action, x, y, button, clicks, scroll_amount)

    async def _mouse_powershell(self, action: MouseAction, x: int, y: int,
                                button: str, clicks: int,
                                scroll_amount: int) -> ActionResult:
        """PowerShell鼠标操控"""
        if action == MouseAction.MOVE:
            cmd = f"[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x},{y})"
        elif action in (MouseAction.CLICK, MouseAction.DOUBLE_CLICK, MouseAction.RIGHT_CLICK):
            btn = "Left" if button == "left" else "Right"
            cmd = (
                f"[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x},{y}); "
                f"Start-Sleep -Milliseconds 50; "
                f"$wsh = New-Object -ComObject WScript.Shell; "
            )
        else:
            cmd = f"[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x},{y})"

        result = await executor.execute_powershell(
            f"Add-Type -AssemblyName System.Windows.Forms; {cmd}"
        )
        return ActionResult(success=result.return_code == 0, action=action.value)

    async def _mouse_linux(self, action: MouseAction, x: int, y: int,
                           button: str, clicks: int,
                           scroll_amount: int) -> ActionResult:
        """Linux鼠标操控 - 使用xdotool"""
        cmd_map = {
            MouseAction.CLICK: f"xdotool mousemove {x} {y} click {1 if button == 'left' else 3}",
            MouseAction.DOUBLE_CLICK: f"xdotool mousemove {x} {y} click --repeat 2 1",
            MouseAction.RIGHT_CLICK: f"xdotool mousemove {x} {y} click 3",
            MouseAction.MOVE: f"xdotool mousemove {x} {y}",
            MouseAction.SCROLL_DOWN: f"xdotool mousemove {x} {y} click 5 --repeat {scroll_amount}",
            MouseAction.SCROLL_UP: f"xdotool mousemove {x} {y} click 4 --repeat {scroll_amount}",
        }
        cmd = cmd_map.get(action, f"xdotool mousemove {x} {y}")
        result = await executor.execute(cmd, timeout=5)
        return ActionResult(success=result.success, action=action.value)

    # ==================== 键盘操控 ====================

    async def type_text(self, text: str, interval: float = 0.05) -> ActionResult:
        """输入文本"""
        try:
            if self._is_windows:
                try:
                    import pyautogui
                    pyautogui.write(text, interval=interval)
                    return ActionResult(success=True, action="type")
                except ImportError:
                    result = await executor.execute_powershell(
                        f"Add-Type -AssemblyName System.Windows.Forms; "
                        f"[System.Windows.Forms.SendKeys]::SendWait('{text}')"
                    )
                    return ActionResult(success=result.success, action="type")
            else:
                result = await executor.execute(f"xdotool type --delay {int(interval*1000)} '{text}'")
                return ActionResult(success=result.success, action="type")
        except Exception as e:
            return ActionResult(success=False, action="type", error=str(e))

    async def hotkey(self, *keys: str) -> ActionResult:
        """按下快捷键组合"""
        try:
            if self._is_windows:
                try:
                    import pyautogui
                    pyautogui.hotkey(*keys)
                    return ActionResult(success=True, action="hotkey")
                except ImportError:
                    # PowerShell回退
                    send_keys = ""
                    for k in keys:
                        k_lower = k.lower()
                        if k_lower in ("ctrl", "control"):
                            send_keys += "^"
                        elif k_lower == "alt":
                            send_keys += "%"
                        elif k_lower == "shift":
                            send_keys += "+"
                        else:
                            send_keys += k
                    result = await executor.execute_powershell(
                        f"Add-Type -AssemblyName System.Windows.Forms; "
                        f"[System.Windows.Forms.SendKeys]::SendWait('{send_keys}')"
                    )
                    return ActionResult(success=result.success, action="hotkey")
            else:
                key_str = "+".join(keys)
                result = await executor.execute(f"xdotool key {key_str}")
                return ActionResult(success=result.success, action="hotkey")
        except Exception as e:
            return ActionResult(success=False, action="hotkey", error=str(e))

    async def press_key(self, key: str) -> ActionResult:
        """按下单个按键"""
        try:
            if self._is_windows:
                try:
                    import pyautogui
                    pyautogui.press(key)
                    return ActionResult(success=True, action="press")
                except ImportError:
                    result = await executor.execute_powershell(
                        f"Add-Type -AssemblyName System.Windows.Forms; "
                        f"[System.Windows.Forms.SendKeys]::SendWait('{{{key}}}')"
                    )
                    return ActionResult(success=result.success, action="press")
            else:
                result = await executor.execute(f"xdotool key {key}")
                return ActionResult(success=result.success, action="press")
        except Exception as e:
            return ActionResult(success=False, action="press", error=str(e))

    # ==================== 高级操作 ====================

    async def launch_app(self, app_path: str, args: str = None) -> ActionResult:
        """启动应用"""
        if self._is_windows:
            cmd = f"Start-Process '{app_path}'"
            if args:
                cmd += f" -ArgumentList '{args}'"
            result = await executor.execute_powershell(cmd)
        else:
            result = await executor.execute(f"nohup {app_path} {args or ''} &")
        return ActionResult(success=result.success, action="launch")

    async def get_screen_size(self) -> Tuple[int, int]:
        """获取屏幕尺寸"""
        if self._screen_size:
            return self._screen_size
        try:
            import pyautogui
            self._screen_size = pyautogui.size()
            return self._screen_size
        except ImportError:
            if self._is_windows:
                result = await executor.execute_powershell(
                    "(Get-WmiObject Win32_VideoController).CurrentHorizontalResolution"
                )
                # 简单回退
                pass
            return (1920, 1080)

    async def clipboard_copy(self) -> str:
        """读取剪贴板"""
        if self._is_windows:
            result = await executor.execute_powershell("Get-Clipboard")
            return result.stdout if result.success else ""
        else:
            result = await executor.execute("xclip -selection clipboard -o", timeout=3)
            return result.stdout if result.success else ""

    async def clipboard_paste(self, text: str) -> bool:
        """写入剪贴板"""
        if self._is_windows:
            result = await executor.execute_powershell(f"Set-Clipboard -Value '{text}'")
            return result.success
        else:
            result = await executor.execute(f"echo -n '{text}' | xclip -selection clipboard")
            return result.success

    async def list_windows(self) -> List[Dict]:
        """获取窗口列表"""
        if self._is_windows:
            result = await executor.execute_powershell(
                "Get-Process | Where-Object { $_.MainWindowHandle -ne 0 } | "
                "Select-Object Id, ProcessName, MainWindowTitle | ConvertTo-Json"
            )
            if result.success and result.stdout.strip():
                try:
                    import json
                    windows = json.loads(result.stdout)
                    if isinstance(windows, dict):
                        windows = [windows]
                    return windows[:20]
                except json.JSONDecodeError:
                    pass
        else:
            result = await executor.execute("wmctrl -l", timeout=3)
            if result.success:
                windows = []
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(None, 3)
                    if len(parts) >= 4:
                        windows.append({"id": parts[0], "title": parts[3]})
                return windows
        return []

    async def find_and_click(self, description: str) -> ActionResult:
        """
        AI查找并点击屏幕元素
        先截图 → AI识别元素位置 → 点击
        """
        screenshot = await self.screenshot(ocr=True)
        # 这里需要视觉模型来识别元素位置
        # 当前返回提示信息
        return ActionResult(
            success=False, action="find_and_click",
            error=f"需要视觉模型来定位: {description}"
        )


# 全局单例
agent = DesktopAgent()
