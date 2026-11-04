"""
EonOS MCP 工具管理器
全部保留原有MCP工具 + AI鼠标操控增强
统一管理所有工具的注册、发现、调用
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core import EventBus, EventType, Event, bus
from ..core.command import executor
from ..agent import agent

logger = logging.getLogger("eonos.mcp")


class ToolCategory(str, Enum):
    SYSTEM = "system"
    FILE = "file"
    NETWORK = "network"
    DATA = "data"
    DEV = "dev"
    AI_MOUSE = "ai_mouse"  # AI鼠标操控类工具


@dataclass
class ToolInfo:
    name: str
    description: str
    category: ToolCategory
    server_path: Optional[str] = None  # MCP server路径
    handler: Optional[Callable] = None  # 内置处理函数
    is_ai_mouse: bool = False  # 是否是AI鼠标替代
    available: bool = False


class MCPManager:
    """
    MCP工具管理器

    策略: 全部保留原有MCP工具 + AI鼠标操控增强
    - 原有MCP工具保持独立server进程
    - AI鼠标操控作为内置工具，无需额外进程
    - 统一工具发现和调用接口
    """

    def __init__(self, event_bus: EventBus = None):
        self.bus = event_bus or bus
        self._tools: Dict[str, ToolInfo] = {}
        self._processes: Dict[str, subprocess.Popen] = {}
        self._mcp_base_dir = self._find_mcp_dir()
        self._python_path = sys.executable
        self._initialized = False

    def _find_mcp_dir(self) -> str:
        """查找MCP工具目录"""
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidates = [
            os.path.join(base, "cursor--------mcp"),
            os.path.join(base, "trae--aidamoxing", "mcp"),
        ]
        for d in candidates:
            if os.path.isdir(d):
                return d
        return ""

    async def initialize(self) -> bool:
        """初始化所有工具"""
        self._register_builtin_tools()
        self._register_mcp_tools()
        self._register_ai_mouse_tools()
        self._initialized = True
        logger.info(f"MCP Manager initialized: {len(self._tools)} tools registered")
        return True

    def _register_builtin_tools(self):
        """注册内置工具"""
        builtins = [
            ToolInfo("eonos.chat", "AI对话", ToolCategory.AI_MOUSE, handler=self._tool_chat, is_ai_mouse=True),
            ToolInfo("eonos.screenshot", "截屏+OCR", ToolCategory.AI_MOUSE, handler=self._tool_screenshot, is_ai_mouse=True),
            ToolInfo("eonos.click", "AI鼠标点击", ToolCategory.AI_MOUSE, handler=self._tool_click, is_ai_mouse=True),
            ToolInfo("eonos.type", "AI键盘输入", ToolCategory.AI_MOUSE, handler=self._tool_type, is_ai_mouse=True),
            ToolInfo("eonos.hotkey", "AI快捷键", ToolCategory.AI_MOUSE, handler=self._tool_hotkey, is_ai_mouse=True),
            ToolInfo("eonos.command", "执行命令(带完成通知)", ToolCategory.SYSTEM, handler=self._tool_command),
            ToolInfo("eonos.launch", "启动应用", ToolCategory.AI_MOUSE, handler=self._tool_launch, is_ai_mouse=True),
            ToolInfo("eonos.clipboard", "剪贴板操作", ToolCategory.AI_MOUSE, handler=self._tool_clipboard, is_ai_mouse=True),
            ToolInfo("eonos.windows", "窗口列表", ToolCategory.AI_MOUSE, handler=self._tool_windows, is_ai_mouse=True),
        ]
        for tool in builtins:
            tool.available = True
            self._tools[tool.name] = tool

    def _register_mcp_tools(self):
        """注册原有MCP工具"""
        if not self._mcp_base_dir:
            return

        # 从.mcp.json加载
        mcp_config_path = os.path.join(self._mcp_base_dir, ".mcp.json")
        if os.path.exists(mcp_config_path):
            try:
                with open(mcp_config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                for name, conf in config.items():
                    server_path = conf.get("args", [""])[0] if conf.get("args") else None
                    if server_path and os.path.exists(server_path):
                        self._tools[f"mcp.{name}"] = ToolInfo(
                            name=f"mcp.{name}",
                            description=f"MCP工具: {name}",
                            category=self._guess_category(name),
                            server_path=server_path,
                            available=True,
                        )
            except Exception as e:
                logger.error(f"Failed to load MCP config: {e}")

        # 扫描目录中的server.py
        for item in os.listdir(self._mcp_base_dir):
            item_path = os.path.join(self._mcp_base_dir, item)
            server_py = os.path.join(item_path, "server.py")
            if os.path.isdir(item_path) and os.path.exists(server_py):
                tool_name = f"mcp.{item.replace('-', '_').replace('-mcp', '')}"
                if tool_name not in self._tools:
                    self._tools[tool_name] = ToolInfo(
                        name=tool_name,
                        description=f"MCP工具: {item}",
                        category=self._guess_category(item),
                        server_path=server_py,
                        available=True,
                    )

    def _register_ai_mouse_tools(self):
        """注册AI鼠标操控增强工具 - 可替代简单MCP"""
        ai_mouse_tools = [
            ToolInfo("ai_mouse.screenshot", "AI鼠标截屏(替代screenshot-mcp/ocr-mcp)",
                     ToolCategory.AI_MOUSE, handler=self._tool_screenshot, is_ai_mouse=True),
            ToolInfo("ai_mouse.clipboard_read", "AI鼠标读取剪贴板(替代clipboard-mcp)",
                     ToolCategory.AI_MOUSE, handler=self._tool_clipboard_read, is_ai_mouse=True),
            ToolInfo("ai_mouse.clipboard_write", "AI鼠标写入剪贴板(替代clipboard-mcp)",
                     ToolCategory.AI_MOUSE, handler=self._tool_clipboard_write, is_ai_mouse=True),
            ToolInfo("ai_mouse.app_launch", "AI鼠标启动应用(替代startup-mcp)",
                     ToolCategory.AI_MOUSE, handler=self._tool_launch, is_ai_mouse=True),
            ToolInfo("ai_mouse.window_list", "AI鼠标窗口列表(替代window-manager-mcp)",
                     ToolCategory.AI_MOUSE, handler=self._tool_windows, is_ai_mouse=True),
        ]
        for tool in ai_mouse_tools:
            tool.available = True
            self._tools[tool.name] = tool

    def _guess_category(self, name: str) -> ToolCategory:
        name_lower = name.lower()
        if any(kw in name_lower for kw in ["git", "docker", "code", "registry"]):
            return ToolCategory.DEV
        if any(kw in name_lower for kw in ["network", "wifi", "web", "fetch", "url"]):
            return ToolCategory.NETWORK
        if any(kw in name_lower for kw in ["excel", "csv", "sqlite", "json", "pdf"]):
            return ToolCategory.DATA
        if any(kw in name_lower for kw in ["file", "disk", "backup", "archive"]):
            return ToolCategory.FILE
        return ToolCategory.SYSTEM

    # ==================== 工具调用 ====================

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
        """统一工具调用接口"""
        arguments = arguments or {}
        correlation_id = self.bus.create_correlation_id()

        # 发出工具调用事件
        await self.bus.emit_simple(
            EventType.MCP_TOOL_CALL, "mcp",
            {"tool": tool_name, "args": arguments},
            correlation_id=correlation_id
        )

        tool = self._tools.get(tool_name)
        if not tool:
            result = {"error": f"Tool not found: {tool_name}"}
        elif tool.handler:
            try:
                result = await tool.handler(arguments)
            except Exception as e:
                result = {"error": str(e)}
        elif tool.server_path:
            result = await self._call_mcp_server(tool.server_path, arguments)
        else:
            result = {"error": f"Tool has no handler: {tool_name}"}

        # 发出工具完成事件
        await self.bus.emit_simple(
            EventType.MCP_TOOL_COMPLETE, "mcp",
            {"tool": tool_name, "success": "error" not in result},
            correlation_id=correlation_id
        )

        return result

    async def _call_mcp_server(self, server_path: str, arguments: Dict) -> Dict:
        """调用MCP server进程"""
        try:
            # MCP协议: 通过stdin/stdout通信
            request = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": list(arguments.keys())[0] if arguments else "", "arguments": arguments},
                "id": 1,
            }

            proc = subprocess.Popen(
                [self._python_path, server_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = proc.communicate(
                input=json.dumps(request) + "\n",
                timeout=30,
            )

            if stdout:
                try:
                    return json.loads(stdout)
                except json.JSONDecodeError:
                    return {"result": stdout[:2000]}
            return {"error": stderr[:500] if stderr else "No output"}

        except subprocess.TimeoutExpired:
            proc.kill()
            return {"error": "MCP server timeout"}
        except Exception as e:
            return {"error": str(e)}

    # ==================== 内置工具处理函数 ====================

    async def _tool_chat(self, args: Dict) -> Dict:
        from ..engine import engine
        result = await engine.chat(args.get("message", ""), config=args.get("config"))
        return {"response": result.get("response", ""), "session_id": result.get("session_id")}

    async def _tool_screenshot(self, args: Dict) -> Dict:
        result = await agent.screenshot(ocr=args.get("ocr", True))
        return {
            "image_data": result.image_data[:100] + "..." if len(result.image_data) > 100 else result.image_data,
            "width": result.width, "height": result.height,
            "ocr_text": result.ocr_text,
        }

    async def _tool_click(self, args: Dict) -> Dict:
        result = await agent.click(args.get("x", 0), args.get("y", 0),
                                   args.get("button", "left"))
        return {"success": result.success, "error": result.error}

    async def _tool_type(self, args: Dict) -> Dict:
        result = await agent.type_text(args.get("text", ""))
        return {"success": result.success, "error": result.error}

    async def _tool_hotkey(self, args: Dict) -> Dict:
        keys = args.get("keys", [])
        result = await agent.hotkey(*keys)
        return {"success": result.success, "error": result.error}

    async def _tool_command(self, args: Dict) -> Dict:
        result = await executor.execute(
            args.get("command", ""),
            timeout=args.get("timeout", 30),
        )
        return {
            "success": result.success,
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:500],
            "return_code": result.return_code,
            "duration": result.duration,
        }

    async def _tool_launch(self, args: Dict) -> Dict:
        result = await agent.launch_app(args.get("path", ""), args.get("args"))
        return {"success": result.success, "error": result.error}

    async def _tool_clipboard(self, args: Dict) -> Dict:
        action = args.get("action", "read")
        if action == "read":
            text = await agent.clipboard_copy()
            return {"text": text}
        else:
            success = await agent.clipboard_paste(args.get("text", ""))
            return {"success": success}

    async def _tool_clipboard_read(self, args: Dict) -> Dict:
        text = await agent.clipboard_copy()
        return {"text": text}

    async def _tool_clipboard_write(self, args: Dict) -> Dict:
        success = await agent.clipboard_paste(args.get("text", ""))
        return {"success": success}

    async def _tool_windows(self, args: Dict) -> Dict:
        windows = await agent.list_windows()
        return {"windows": windows}

    # ==================== 查询接口 ====================

    def list_tools(self, category: ToolCategory = None,
                   ai_mouse_only: bool = False) -> List[Dict]:
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        if ai_mouse_only:
            tools = [t for t in tools if t.is_ai_mouse]
        return [
            {
                "name": t.name,
                "description": t.description,
                "category": t.category.value,
                "is_ai_mouse": t.is_ai_mouse,
                "available": t.available,
            }
            for t in tools
        ]

    def get_tool(self, name: str) -> Optional[ToolInfo]:
        return self._tools.get(name)


# 全局单例
mcp_manager = MCPManager()
