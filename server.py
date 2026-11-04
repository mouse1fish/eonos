"""
EonOS 统一Web服务器
整合 AI聊天 + 桌面操控 + MCP工具 + 系统状态
"""

import asyncio
import json
import logging
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading

from .core import EventBus, EventType, bus
from .core.command import executor
from .engine import engine, ThinkMode, InferenceConfig, ComputeMode
from .agent import agent
from .mcp import mcp_manager
from .compat import compat

logger = logging.getLogger("eonos.server")


class EonOSHandler(BaseHTTPRequestHandler):
    """EonOS HTTP请求处理器"""

    def log_message(self, format, *args):
        logger.debug(f"{self.address_string()} - {format % args}")

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        params = parse_qs(url.query)

        routes = {
            "/": self._handle_index,
            "/api/status": self._handle_status,
            "/api/tools": self._handle_tools,
            "/api/sessions": self._handle_sessions,
            "/api/platform": self._handle_platform,
            "/api/events": self._handle_events,
            "/api/hardware": self._handle_hardware,
        }

        handler = routes.get(path)
        if handler:
            handler(params)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        path = url.path

        routes = {
            "/api/chat": self._handle_chat,
            "/api/screenshot": self._handle_screenshot,
            "/api/mouse": self._handle_mouse,
            "/api/keyboard": self._handle_keyboard,
            "/api/command": self._handle_command,
            "/api/tool": self._handle_tool_call,
            "/api/launch": self._handle_launch,
        }

        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._send_json({"error": "Not found"}, 404)

    # ==================== GET处理 ====================

    def _handle_index(self, params=None):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self._get_ui_html().encode("utf-8"))

    def _handle_status(self, params=None):
        hw_info = engine.get_hardware_info()
        self._send_json({
            "status": "ok",
            "engine_ready": engine.is_ready,
            "backend": engine.backend.value,
            "compute_mode": hw_info.get("compute_mode", "auto"),
            "hardware_summary": hw_info.get("summary", "unknown"),
            "tools_count": len(mcp_manager.list_tools()),
            "platform": compat.platform_info.system if compat._platform_info else "unknown",
            "uptime": time.time() - _start_time,
            "version": "1.0.0",
        })

    def _handle_tools(self, params=None):
        category = params.get("category", [None])[0]
        ai_mouse = params.get("ai_mouse", ["false"])[0] == "true"
        tools = mcp_manager.list_tools(
            category=category,
            ai_mouse_only=ai_mouse,
        )
        self._send_json({"tools": tools, "count": len(tools)})

    def _handle_sessions(self, params=None):
        self._send_json({"sessions": engine.list_sessions()})

    def _handle_platform(self, params=None):
        info = compat.platform_info
        self._send_json({
            "system": info.system,
            "is_wsl": info.is_wsl,
            "has_docker": info.has_docker,
            "has_winboat": info.has_winboat,
            "python": info.python_path,
        })

    def _handle_events(self, params=None):
        limit = int(params.get("limit", ["20"])[0])
        events = bus.get_history(limit=limit)
        self._send_json({
            "events": [
                {"type": e.type.value, "source": e.source,
                 "data": e.data, "time": e.timestamp}
                for e in events
            ]
        })

    def _handle_hardware(self, params=None):
        """硬件信息API - 万能系统核心"""
        engine_hw = engine.get_hardware_info()
        compat_hw = compat.get_hardware_info()
        self._send_json({
            "engine": engine_hw,
            "platform": compat_hw,
            "compute_modes": [m.value for m in ComputeMode],
        })

    # ==================== POST处理 ====================

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

    def _handle_screenshot(self):
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(agent.screenshot(ocr=True))
        loop.close()

        self._send_json({
            "width": result.width, "height": result.height,
            "ocr_text": result.ocr_text,
            "image_data": result.image_data[:200] + "..." if len(result.image_data) > 200 else result.image_data,
        })

    def _handle_mouse(self):
        body = self._read_body()
        action = body.get("action", "click")
        x = body.get("x", 0)
        y = body.get("y", 0)

        loop = asyncio.new_event_loop()
        if action == "click":
            result = loop.run_until_complete(agent.click(x, y, body.get("button", "left")))
        elif action == "double_click":
            result = loop.run_until_complete(agent.double_click(x, y))
        elif action == "right_click":
            result = loop.run_until_complete(agent.right_click(x, y))
        elif action == "move":
            result = loop.run_until_complete(agent.move(x, y))
        elif action == "scroll":
            result = loop.run_until_complete(agent.scroll(x, y, body.get("amount", 3)))
        else:
            result = loop.run_until_complete(agent.click(x, y))
        loop.close()

        self._send_json({"success": result.success, "action": result.action, "error": result.error})

    def _handle_keyboard(self):
        body = self._read_body()
        action = body.get("action", "type")

        loop = asyncio.new_event_loop()
        if action == "type":
            result = loop.run_until_complete(agent.type_text(body.get("text", "")))
        elif action == "hotkey":
            result = loop.run_until_complete(agent.hotkey(*body.get("keys", [])))
        elif action == "press":
            result = loop.run_until_complete(agent.press_key(body.get("key", "enter")))
        else:
            result = loop.run_until_complete(agent.type_text(body.get("text", "")))
        loop.close()

        self._send_json({"success": result.success, "action": result.action, "error": result.error})

    def _handle_command(self):
        body = self._read_body()
        command = body.get("command", "")
        timeout = body.get("timeout", 30)

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(executor.execute(command, timeout=timeout))
        loop.close()

        self._send_json({
            "success": result.success,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.return_code,
            "duration": result.duration,
            # 核心改进: 命令完成通知已通过事件总线发出
            "notification": "Command completion event emitted via EventBus",
        })

    def _handle_tool_call(self):
        body = self._read_body()
        tool_name = body.get("tool", "")
        arguments = body.get("arguments", {})

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(mcp_manager.call_tool(tool_name, arguments))
        loop.close()

        self._send_json(result)

    def _handle_launch(self):
        body = self._read_body()
        app_path = body.get("path", "")
        args = body.get("args")

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(agent.launch_app(app_path, args))
        loop.close()

        self._send_json({"success": result.success, "error": result.error})

    # ==================== UI ====================

    def _get_ui_html(self) -> str:
        return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EonOS - Universal AI System</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0f;color:#e0e0e0;height:100vh;display:flex;flex-direction:column;overflow:hidden}
.header{display:flex;align-items:center;padding:0 16px;background:linear-gradient(135deg,#0d0d15,#1a1a2e);border-bottom:1px solid #1a1a2e;height:44px;flex-shrink:0}
.header .logo{font-size:15px;font-weight:700;color:#00ff88;margin-right:16px}
.header .logo .ver{font-size:10px;color:#555;font-weight:400;margin-left:6px}
.header .nav{display:flex;gap:2px;flex:1}
.header .nav button{padding:8px 18px;background:transparent;border:none;border-bottom:2px solid transparent;color:#666;font-size:13px;cursor:pointer;transition:all .2s}
.header .nav button:hover{color:#aaa}
.header .nav button.active{color:#00ff88;border-bottom-color:#00ff88}
.header .status{display:flex;align-items:center;gap:8px;margin-left:auto}
.badge{padding:2px 8px;border-radius:4px;font-size:10px}
.badge.ok{background:#003322;color:#00ff88}
.badge.loading{background:#332200;color:#ffaa00}
.badge.gpu{background:#220033;color:#aa88ff}
.badge.cpu{background:#003322;color:#88ffaa}
.tab-content{flex:1;display:none;overflow:hidden}
.tab-content.active{display:flex;flex-direction:column}
.chat-area{flex:1;overflow-y:auto;padding:16px 20px}
.msg{margin-bottom:14px;display:flex;gap:10px}
.msg.user{flex-direction:row-reverse}
.msg .avatar{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.msg.ai .avatar{background:#1a1a2e;border:1px solid #00ff88}
.msg.user .avatar{background:#1a1a2e;border:1px solid #4a9eff}
.msg .bubble{max-width:72%;padding:10px 14px;border-radius:12px;font-size:13px;line-height:1.6;word-break:break-word}
.msg.ai .bubble{background:#1a1a2e;border:1px solid #2a2a4e}
.msg.user .bubble{background:#16213e;border:1px solid #2a4a6e}
.input-area{padding:12px 20px;background:#0d0d15;border-top:1px solid #1a1a2e;display:flex;gap:10px;align-items:flex-end}
.input-area textarea{flex:1;background:#1a1a2e;border:1px solid #2a2a4e;border-radius:8px;color:#e0e0e0;padding:10px;font-size:13px;resize:none;height:40px;max-height:100px;font-family:inherit;outline:none}
.input-area textarea:focus{border-color:#00ff88}
.input-area button{padding:10px 20px;background:#00ff88;color:#000;border:none;border-radius:8px;font-size:13px;font-weight:bold;cursor:pointer;white-space:nowrap}
.input-area button:hover{background:#00cc6a}
.welcome{text-align:center;padding:50px 20px;color:#555}
.welcome h2{color:#00ff88;font-size:22px;margin-bottom:12px}
.welcome p{margin-bottom:6px;font-size:13px}
.welcome .cmd{color:#888;font-family:monospace;background:#1a1a2e;padding:2px 8px;border-radius:4px}
.tools-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px;padding:16px;overflow-y:auto}
.tool-card{padding:10px;border:1px solid #2a2a4e;border-radius:8px;cursor:pointer;transition:all .2s}
.tool-card:hover{border-color:#00ff88;background:#111122}
.tool-card .name{font-size:12px;color:#00ff88;font-weight:500}
.tool-card .desc{font-size:11px;color:#666;margin-top:2px}
.tool-card .tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:9px;margin-top:4px}
.tool-card .tag.ai{background:#003322;color:#00ff88}
.tool-card .tag.mcp{background:#001a33;color:#4a9eff}
.status-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;padding:16px;overflow-y:auto}
.status-card{background:#1a1a2e;border:1px solid #2a2a4e;border-radius:12px;padding:20px}
.status-card h3{font-size:13px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px}
.status-card .value{font-size:28px;font-weight:bold;color:#00ff88}
.status-card .sub{font-size:12px;color:#666;margin-top:4px}
.status-card .detail{font-size:11px;color:#555;margin-top:8px;line-height:1.5}
.hw-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;padding:16px;overflow-y:auto}
.hw-card{background:#1a1a2e;border:1px solid #2a2a4e;border-radius:12px;padding:20px}
.hw-card h3{font-size:13px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.hw-card h3 .icon{font-size:16px}
.hw-card .big{font-size:24px;font-weight:bold;color:#00ff88}
.hw-card .info{font-size:12px;color:#888;margin-top:4px;line-height:1.6}
.hw-card .gpu-item{padding:8px 0;border-bottom:1px solid #222}
.hw-card .gpu-item:last-child{border-bottom:none}
.hw-card .gpu-name{color:#aa88ff;font-size:13px;font-weight:500}
.hw-card .gpu-mem{color:#666;font-size:11px}
.mode-badge{display:inline-block;padding:4px 12px;border-radius:6px;font-size:12px;font-weight:bold}
.mode-badge.multi-gpu{background:#220033;color:#aa88ff}
.mode-badge.single-gpu{background:#003322;color:#88ffaa}
.mode-badge.hybrid{background:#003333;color:#88ffff}
.mode-badge.multi-cpu{background:#332200;color:#ffaa88}
.mode-badge.single-cpu{background:#222;color:#aaa}
.event-log{padding:16px;overflow-y:auto;font-family:monospace;font-size:11px}
.event-item{padding:4px 8px;border-bottom:1px solid #111}
.event-item .type{color:#00ff88}
.event-item .source{color:#4a9eff}
.event-item .time{color:#555;float:right}
</style>
</head>
<body>
<div class="header">
<div class="logo">EonOS<span class="ver">v1.0 Universal</span></div>
<div class="nav">
<button class="active" onclick="switchTab('chat',this)">Chat</button>
<button onclick="switchTab('tools',this)">Tools</button>
<button onclick="switchTab('status',this)">Status</button>
<button onclick="switchTab('hardware',this)">Hardware</button>
<button onclick="switchTab('events',this)">Events</button>
</div>
<div class="status">
<span class="badge loading" id="engineBadge">Engine</span>
<span class="badge" id="hwBadge">--</span>
</div>
</div>

<div class="tab-content active" id="tab-chat">
<div class="chat-area" id="chatArea">
<div class="welcome">
<h2>EonOS Universal</h2>
<p>Multi-CPU / Multi-GPU / Single-CPU / Single-GPU Adaptive</p>
<p style="margin-top:16px">Commands:</p>
<p><span class="cmd">screenshot</span> - Take screenshot</p>
<p><span class="cmd">run: dir</span> - Execute command</p>
<p><span class="cmd">click 100 200</span> - Mouse click</p>
<p><span class="cmd">tools</span> - List all tools</p>
<p><span class="cmd">hardware</span> - Show hardware info</p>
</div>
</div>
<div class="input-area">
<textarea id="input" placeholder="Type message... (Enter to send)" rows="1"></textarea>
<button onclick="sendMessage()">Send</button>
</div>
</div>

<div class="tab-content" id="tab-tools">
<div class="tools-grid" id="toolsGrid"></div>
</div>

<div class="tab-content" id="tab-status">
<div class="status-grid" id="statusGrid"></div>
</div>

<div class="tab-content" id="tab-hardware">
<div class="hw-grid" id="hwGrid"></div>
</div>

<div class="tab-content" id="tab-events">
<div class="event-log" id="eventLog"></div>
</div>

<script>
function switchTab(name,btn){
document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));
document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('active'));
document.getElementById('tab-'+name).classList.add('active');
btn.classList.add('active');
if(name==='tools')loadTools();
if(name==='status')loadStatus();
if(name==='hardware')loadHardware();
if(name==='events')loadEvents();
}

async function api(path,method='GET',body=null){
const opts={method,headers:{'Content-Type':'application/json'}};
if(body)opts.body=JSON.stringify(body);
const res=await fetch(path,opts);
return res.json();
}

async function sendMessage(){
const input=document.getElementById('input');
const msg=input.value.trim();
if(!msg)return;
input.value='';
addMsg('user',msg);

if(msg==='screenshot'||msg==='截图'){
const r=await api('/api/screenshot','POST');
addMsg('ai',`Screenshot: ${r.width}x${r.height}${r.ocr_text?'\\nOCR: '+r.ocr_text.substring(0,200):''}`);
return;
}
if(msg.startsWith('run:')){
const cmd=msg.substring(4).trim();
const r=await api('/api/command','POST',{command:cmd});
addMsg('ai',`$ ${cmd}\\n${r.stdout||r.stderr}\\n[${r.duration}] Completion event emitted`);
return;
}
if(msg.startsWith('click ')){
const[x,y]=msg.substring(6).split(' ').map(Number);
const r=await api('/api/mouse','POST',{action:'click',x,y});
addMsg('ai',`Click (${x},${y}): ${r.success?'OK':'FAIL'}`);
return;
}
if(msg==='tools'){
switchTab('tools',document.querySelectorAll('.nav button')[1]);
return;
}
if(msg==='hardware'||msg==='硬件'){
switchTab('hardware',document.querySelectorAll('.nav button')[3]);
return;
}

const r=await api('/api/chat','POST',{message:msg});
addMsg('ai',r.response||r.error||'No response');
if(r.session_id)window._sessionId=r.session_id;
}

function addMsg(role,text){
const area=document.getElementById('chatArea');
const div=document.createElement('div');
div.className='msg '+role;
div.innerHTML=`<div class="avatar">${role==='ai'?'AI':'U'}</div><div class="bubble">${text.replace(/\\n/g,'<br>')}</div>`;
area.appendChild(div);
area.scrollTop=area.scrollHeight;
}

async function loadTools(){
const r=await api('/api/tools');
const grid=document.getElementById('toolsGrid');
grid.innerHTML=r.tools.map(t=>`<div class="tool-card"><div class="name">${t.name}</div><div class="desc">${t.description}</div><span class="tag ${t.is_ai_mouse?'ai':'mcp'}">${t.is_ai_mouse?'AI Mouse':'MCP'}</span></div>`).join('');
}

async function loadStatus(){
const r=await api('/api/status');
const grid=document.getElementById('statusGrid');
const modeClass=r.compute_mode||'single-cpu';
grid.innerHTML=`
<div class="status-card"><h3>Engine</h3><div class="value">${r.engine_ready?'Ready':'Loading'}</div><div class="sub">${r.backend} / ${r.compute_mode}</div></div>
<div class="status-card"><h3>Hardware</h3><div class="value" style="font-size:16px">${r.hardware_summary||'detecting...'}</div><div class="sub"><span class="mode-badge ${modeClass}">${r.compute_mode||'auto'}</span></div></div>
<div class="status-card"><h3>Tools</h3><div class="value">${r.tools_count}</div><div class="sub">Available</div></div>
<div class="status-card"><h3>Platform</h3><div class="value">${r.platform}</div><div class="sub">EonOS v${r.version}</div></div>
`;
document.getElementById('engineBadge').className='badge '+(r.engine_ready?'ok':'loading');
document.getElementById('engineBadge').textContent=r.engine_ready?'Ready':'Loading';
const hwB=document.getElementById('hwBadge');
hwB.textContent=r.compute_mode||'--';
hwB.className='badge '+(r.compute_mode&&r.compute_mode.includes('gpu')?'gpu':'cpu');
}

async function loadHardware(){
const r=await api('/api/hardware');
const hw=r.engine||{};
const plat=r.platform||{};
const grid=document.getElementById('hwGrid');
const mode=hw.compute_mode||'auto';
const modeClass=mode.replace('_','-');
let gpuHtml='';
if(hw.gpus&&hw.gpus.length){
gpuHtml=hw.gpus.map(g=>`<div class="gpu-item"><div class="gpu-name">GPU ${g.index}: ${g.name}</div><div class="gpu-mem">VRAM: ${g.vram_total_mb}MB${g.vram_free_mb?' (Free: '+g.vram_free_mb+'MB)':''}${g.compute_capability?' CC: '+g.compute_capability:''}</div></div>`).join('');
}else{
gpuHtml='<div class="info">No GPU detected - CPU-only mode</div>';
}
grid.innerHTML=`
<div class="hw-card"><h3><span class="icon">&#9881;</span> Compute Mode</h3><div class="big"><span class="mode-badge ${modeClass}">${mode.toUpperCase()}</span></div><div class="info">Batch: ${hw.recommended?.batch_size||1} | Threads: ${hw.recommended?.threads||1} | Ctx: ${hw.recommended?.ctx_size||2048} | GPU Layers: ${hw.recommended?.gpu_layers||0}</div></div>
<div class="hw-card"><h3><span class="icon">&#9775;</span> CPU</h3><div class="big">${hw.cpu_count||1} Cores</div><div class="info">${hw.cpu_name||'Unknown'}<br>Architecture: ${plat.cpu_arch||'unknown'}</div></div>
<div class="hw-card"><h3><span class="icon">&#9889;</span> GPU</h3><div class="big">${hw.gpu_count||0} Device${hw.gpu_count!==1?'s':''}</div>${gpuHtml}<div class="info" style="margin-top:8px">CUDA: ${hw.has_cuda?'Yes':'No'} | ROCm: ${hw.has_rocm?'Yes':'No'} | Metal: ${hw.has_metal?'Yes':'No'} | Vulkan: ${hw.has_vulkan?'Yes':'No'}</div></div>
<div class="hw-card"><h3><span class="icon">&#128190;</span> Memory</h3><div class="big">${plat.ram_total_gb||hw.ram_total_gb||0} GB</div><div class="info">Available: ${plat.ram_available_gb||0} GB<br>Disk: ${plat.disk_total_gb||0} GB (Free: ${plat.disk_free_gb||0} GB)</div></div>
`;
}

async function loadEvents(){
const r=await api('/api/events');
const log=document.getElementById('eventLog');
log.innerHTML=r.events.reverse().map(e=>`<div class="event-item"><span class="type">${e.type}</span> <span class="source">[${e.source}]</span> ${JSON.stringify(e.data).substring(0,100)} <span class="time">${new Date(e.time*1000).toLocaleTimeString()}</span></div>`).join('');
}

document.getElementById('input').addEventListener('keydown',e=>{
if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage();}
});

loadStatus();
setInterval(loadStatus,10000);
</script>
</body>
</html>"""


_start_time = time.time()


def run_server(host: str = "127.0.0.1", port: int = 8000):
    """启动EonOS服务器"""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    logger.info(f"EonOS starting on http://{host}:{port}")

    # 初始化各模块
    try:
        loop = asyncio.new_event_loop()

        # 硬件检测 (最先执行)
        loop.run_until_complete(compat.detect_hardware())
        logger.info("Hardware detected")

        # 初始化引擎 (自动检测硬件并选择计算模式)
        loop.run_until_complete(engine.initialize())
        logger.info("AI Engine initialized")

        # 初始化MCP
        loop.run_until_complete(mcp_manager.initialize())
        logger.info("MCP Manager initialized")

        # 检测平台
        loop.run_until_complete(compat.detect_platform())
        logger.info("Platform detected")

        loop.close()
    except Exception as e:
        logger.error(f"Initialization error: {e}", exc_info=True)
        logger.info("Continuing with partial initialization...")

    # 启动HTTP服务器
    try:
        server = HTTPServer((host, port), EonOSHandler)
        logger.info(f"EonOS ready at http://{host}:{port}")
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.server_close()
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
