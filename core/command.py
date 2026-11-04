"""
EonOS 命令执行器 - 带完成通知的命令系统
借鉴 DS4 的 ds4_session_progress_fn / ds4_generation_done_fn 回调模式
解决: 传统命令行对AI不友好，没有完成提示，AI每次都要去查看
"""

import asyncio
import logging
import os
import platform
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import EventBus, EventType, Event, bus

logger = logging.getLogger("eonos.core.command")


class CommandStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class CommandResult:
    """命令执行结果 - 借鉴DS4的ds4_token_score设计"""
    id: str
    command: str
    status: CommandStatus
    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    start_time: float = 0
    end_time: float = 0
    duration_ms: float = 0
    # 借鉴DS4: 完成回调数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == CommandStatus.COMPLETED and self.return_code == 0

    @property
    def duration(self) -> str:
        if self.duration_ms < 1000:
            return f"{self.duration_ms:.0f}ms"
        return f"{self.duration_ms / 1000:.1f}s"


class CommandExecutor:
    """
    带完成通知的命令执行器

    核心改进: 命令执行完成后主动发出事件通知
    AI 不需要轮询检查命令是否完成，直接订阅 COMMAND_COMPLETE 事件

    借鉴 DS4 设计:
    - ds4_session_progress_fn → COMMAND_PROGRESS 事件
    - ds4_generation_done_fn → COMMAND_COMPLETE 事件
    - ds4_session_sync → wait_for_command()
    """

    def __init__(self, event_bus: EventBus = None):
        self.bus = event_bus or bus
        self._running: Dict[str, asyncio.subprocess.Process] = {}
        self._results: Dict[str, CommandResult] = {}
        self._is_windows = platform.system() == "Windows"

    async def execute(self, command: str, timeout: float = 30.0,
                      cwd: str = None, env: Dict[str, str] = None,
                      shell: bool = True) -> CommandResult:
        """
        执行命令并等待完成，自动发出完成通知
        """
        cmd_id = str(uuid.uuid4())[:12]

        # 发出开始事件
        await self.bus.emit_simple(
            EventType.COMMAND_START, "executor",
            {"command_id": cmd_id, "command": command},
            correlation_id=cmd_id
        )

        result = CommandResult(
            id=cmd_id, command=command,
            status=CommandStatus.RUNNING,
            start_time=time.time()
        )

        try:
            # 构建子进程
            if self._is_windows:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd or os.path.expanduser("~"),
                    env={**os.environ, **(env or {})},
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env={**os.environ, **(env or {})},
                )

            self._running[cmd_id] = proc

            # 等待完成，带超时
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                result.status = CommandStatus.TIMEOUT
                result.stderr = f"Command timed out after {timeout}s"
                await self._emit_complete(result, cmd_id)
                return result

            result.stdout = stdout.decode("utf-8", errors="replace") if stdout else ""
            result.stderr = stderr.decode("utf-8", errors="replace") if stderr else ""
            result.return_code = proc.returncode
            result.status = CommandStatus.COMPLETED if proc.returncode == 0 else CommandStatus.FAILED

        except Exception as e:
            result.status = CommandStatus.FAILED
            result.stderr = str(e)

        result.end_time = time.time()
        result.duration_ms = (result.end_time - result.start_time) * 1000
        self._running.pop(cmd_id, None)
        self._results[cmd_id] = result

        # 发出完成事件 - 核心改进: AI不需要轮询
        await self._emit_complete(result, cmd_id)
        return result

    async def execute_and_notify(self, command: str, **kwargs) -> str:
        """
        执行命令并返回结果字符串
        命令完成后自动通过事件总线通知所有订阅者
        """
        result = await self.execute(command, **kwargs)
        return result.stdout if result.success else result.stderr

    async def execute_powershell(self, command: str, timeout: float = 30.0) -> CommandResult:
        """Windows专用: 执行PowerShell命令"""
        if not self._is_windows:
            return await self.execute(command, timeout=timeout)

        # 使用UTF-8编码
        utf8_cmd = (
            "$OutputEncoding = [System.Text.Encoding]::UTF8; "
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            f"{command}"
        )
        import base64
        encoded = base64.b64encode(utf8_cmd.encode("utf-16le")).decode("ascii")

        shell = "pwsh" if self._find_executable("pwsh") else "powershell"
        full_cmd = f"{shell} -NoProfile -EncodedCommand {encoded}"

        return await self.execute(full_cmd, timeout=timeout)

    async def execute_bash(self, command: str, timeout: float = 30.0) -> CommandResult:
        """Linux专用: 执行Bash命令"""
        return await self.execute(f"bash -c '{command}'", timeout=timeout)

    async def cancel(self, cmd_id: str) -> bool:
        proc = self._running.get(cmd_id)
        if proc:
            proc.kill()
            self._running.pop(cmd_id, None)
            if cmd_id in self._results:
                self._results[cmd_id].status = CommandStatus.CANCELLED
            return True
        return False

    def get_result(self, cmd_id: str) -> Optional[CommandResult]:
        return self._results.get(cmd_id)

    def list_running(self) -> List[Dict]:
        return [
            {"id": cid, "command": "running"}
            for cid in self._running
        ]

    async def _emit_complete(self, result: CommandResult, cmd_id: str):
        event_type = EventType.COMMAND_COMPLETE if result.success else EventType.COMMAND_ERROR
        await self.bus.emit(Event(
            type=event_type, source="executor",
            data={
                "command_id": result.id,
                "command": result.command,
                "status": result.status.value,
                "return_code": result.return_code,
                "stdout": result.stdout[:2000],
                "stderr": result.stderr[:500],
                "duration_ms": result.duration_ms,
                "success": result.success,
            },
            correlation_id=cmd_id,
        ))

    @staticmethod
    def _find_executable(name: str) -> bool:
        import shutil
        return shutil.which(name) is not None


# 全局单例
executor = CommandExecutor()
