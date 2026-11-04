"""
EonOS 统一事件总线
借鉴 DS4 的回调通知机制，所有模块通过事件总线通信
AI 不需要轮询，命令完成后主动推送事件通知
"""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("eonos.bus")


class EventType(str, Enum):
    # 命令生命周期事件 (借鉴DS4 ds4_generation_done_fn)
    COMMAND_START = "command.start"
    COMMAND_PROGRESS = "command.progress"
    COMMAND_COMPLETE = "command.complete"
    COMMAND_ERROR = "command.error"

    # AI引擎事件
    AI_INFERENCE_START = "ai.inference.start"
    AI_INFERENCE_TOKEN = "ai.inference.token"
    AI_INFERENCE_COMPLETE = "ai.inference.complete"
    AI_INFERENCE_ERROR = "ai.inference.error"

    # 桌面操控事件
    AGENT_ACTION_START = "agent.action.start"
    AGENT_ACTION_COMPLETE = "agent.action.complete"
    AGENT_SCREENSHOT = "agent.screenshot"
    AGENT_MOUSE = "agent.mouse"
    AGENT_KEYBOARD = "agent.keyboard"

    # MCP工具事件
    MCP_TOOL_CALL = "mcp.tool.call"
    MCP_TOOL_COMPLETE = "mcp.tool.complete"
    MCP_TOOL_ERROR = "mcp.tool.error"

    # 系统事件
    SYSTEM_READY = "system.ready"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_HEALTH = "system.health"

    # 用户事件
    USER_INPUT = "user.input"
    USER_FEEDBACK = "user.feedback"


@dataclass
class Event:
    type: EventType
    source: str
    data: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    # 借鉴DS4: 关联的命令ID，用于追踪命令生命周期
    correlation_id: Optional[str] = None


class EventBus:
    """
    统一事件总线 - 核心通信层

    设计理念借鉴 DS4 的回调机制:
    - DS4 用 ds4_generation_done_fn 回调通知推理完成
    - EonOS 用事件总线统一所有模块的完成通知
    - AI 不需要轮询，订阅事件即可获得完成通知
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._subscribers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._wildcard_subscribers: List[Callable] = []
        self._event_history: List[Event] = []
        self._max_history = 1000
        self._pending_commands: Dict[str, asyncio.Event] = {}
        self._command_results: Dict[str, Any] = {}
        logger.info("EventBus initialized")

    def subscribe(self, event_type: EventType, handler: Callable) -> None:
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed {handler.__name__} to {event_type}")

    def subscribe_all(self, handler: Callable) -> None:
        self._wildcard_subscribers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: Callable) -> None:
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    async def emit(self, event: Event) -> None:
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history:]

        # 借鉴DS4: 命令完成时释放等待的协程
        if event.correlation_id and event.correlation_id in self._pending_commands:
            self._command_results[event.correlation_id] = event.data
            self._pending_commands[event.correlation_id].set()

        handlers = self._subscribers.get(event.type, []) + self._wildcard_subscribers
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                logger.error(f"Event handler error: {e}")

    async def emit_simple(self, event_type: EventType, source: str,
                          data: Dict[str, Any] = None,
                          correlation_id: str = None) -> None:
        await self.emit(Event(
            type=event_type, source=source,
            data=data or {}, correlation_id=correlation_id
        ))

    async def wait_for_complete(self, correlation_id: str, timeout: float = 60.0) -> Any:
        """
        借鉴DS4的同步等待机制:
        DS4中 ds4_session_sync() 等待推理完成
        EonOS中 wait_for_complete() 等待命令完成事件

        AI发出命令后，不需要轮询，直接await即可
        """
        if correlation_id not in self._pending_commands:
            self._pending_commands[correlation_id] = asyncio.Event()

        try:
            await asyncio.wait_for(
                self._pending_commands[correlation_id].wait(),
                timeout=timeout
            )
            result = self._command_results.pop(correlation_id, None)
            self._pending_commands.pop(correlation_id, None)
            return result
        except asyncio.TimeoutError:
            self._pending_commands.pop(correlation_id, None)
            self._command_results.pop(correlation_id, None)
            logger.warning(f"Command timeout: {correlation_id}")
            return {"error": "timeout", "correlation_id": correlation_id}

    def create_correlation_id(self) -> str:
        return str(uuid.uuid4())[:12]

    def get_history(self, event_type: EventType = None, limit: int = 50) -> List[Event]:
        events = self._event_history
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]

    def clear(self):
        self._event_history.clear()
        self._pending_commands.clear()
        self._command_results.clear()


# 全局单例
bus = EventBus()
