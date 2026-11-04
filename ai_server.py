"""
EonOS AI 层（内部 worker，仅监听 127.0.0.1:8001）

架构定位（2026-11 全 Whisker 化）：
    浏览器/客户端 → :8000 Whisker 原生 httpd（前门，静态路由 + 反向代理）
                       └─ /api/ai/* → 本进程（Python，只做 AI 推理）

    Whisker 侧用 netConnect 中继，上游请求带 Connection: close（EOF 定界）。
    旧的 python -m eonos.server（完整平台，:8000）与本进程不要同时启动。
"""
import asyncio
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from .engine import engine, ThinkMode, InferenceConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ai] %(levelname)s %(message)s")
logger = logging.getLogger("eonos.ai")


class AIHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/ai/health":
            self._send_json({
                "ok": True,
                "engine_ready": getattr(engine, "is_ready", False),
                "backend": getattr(getattr(engine, "backend", None), "value", "unknown"),
            })
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/ai/chat":
            self._handle_chat()
        else:
            self._send_json({"error": "not found"}, 404)

    def _handle_chat(self):
        body = self._read_body()
        message = body.get("message", "")
        session_id = body.get("session_id")
        think_mode = body.get("think_mode", "concise")

        config = InferenceConfig(
            think_mode=ThinkMode(think_mode),
            temperature=body.get("temperature", 0.8),
            max_tokens=body.get("max_tokens", 512),
        )

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            engine.chat(message, session_id, config, body.get("image_data"))
        )
        loop.close()

        self._send_json(result)

    def log_message(self, fmt, *args):
        logger.debug(fmt, *args)


def run_ai_server(host: str = "127.0.0.1", port: int = 8001):
    try:
        loop = asyncio.new_event_loop()
        # 引擎初始化（原 server.py 的精简版：AI 层只关心引擎，不碰桌面/MCP）
        loop.run_until_complete(engine.initialize())
        logger.info("AI Engine initialized (ready=%s)", getattr(engine, "is_ready", False))
        loop.close()
    except Exception as e:
        logger.error("Engine init error: %s", e, exc_info=True)
        logger.info("Continuing with partial initialization...")

    try:
        server = HTTPServer((host, port), AIHandler)
        logger.info("EonOS AI layer ready at http://%s:%s (internal)", host, port)
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.server_close()
    except Exception as e:
        logger.error("Server error: %s", e, exc_info=True)


if __name__ == "__main__":
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8001
    run_ai_server(host, port)
