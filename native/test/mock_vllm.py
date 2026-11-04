"""
mock_vllm.py —— OpenAI 兼容的 vLLM 测试替身（验收 httpd 原生 vLLM 客户端）

刻意用 ensure_ascii=True 输出（\\uXXXX 转义），以验证 Whisker 客户端的 \\u 解码路径。

用法: python mock_vllm.py [port]   默认 8002
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MODELS = ["mock-model-7b"]


class MockVLLM(BaseHTTPRequestHandler):

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=True).encode("ascii")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send({
                "object": "list",
                "data": [{"id": m, "object": "model", "owned_by": "mock"} for m in MODELS],
            })
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            msgs = req.get("messages") or [{}]
            content = msgs[-1].get("content", "")
            self._send({
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": req.get("model", MODELS[0]),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": f"Mock 回复：收到「{content}」"},
                    "finish_reason": "stop",
                }],
            })
        else:
            self._send({"error": "not found"}, 404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8002
    print(f"mock vLLM listening on 127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), MockVLLM).serve_forever()
