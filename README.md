# EonOS

**系统 = Whisker（自研语言，原生进程），模型 = vLLM（现成服务）。**
EonOS 的系统层由 [Whisker 语言](https://github.com/mouse1fish/whisker-github) 编写、
自举编译器构建、**零 libc**；浏览器打开 EonOS 首页时，服务它的是一个用 Whisker 写成的原生 exe。

> 语言与系统分仓：本仓库是**系统**（EonOS）；编译器与语言运行时在
> [`mouse1fish/whisker-github`](https://github.com/mouse1fish/whisker-github)。

## 架构

```
浏览器 / 客户端
      │
      ▼
:8000  httpd.exe（Whisker 原生前门·网关模式，仅绑定 127.0.0.1 —— 唯一对外监听者）
      ├─ 原生路由（Whisker 直答，零 Python）：
      │    GET  /                 读盘发首页（web/index.html）
      │    GET  /ui/*             静态文件（MIME + 路径穿越防护）
      │    GET  /api/frontdoor    网关状态 · /api/time
      │    GET  /api/agent/status 屏幕尺寸/光标 · /api/shot.bmp 截屏大图
      │    POST /api/exec /api/command   命令执行器（管道捕获，上限 64KB）
      │    POST /api/screenshot /api/mouse /api/type   agent 原语（截图/鼠标/键盘）
      │    POST /api/ai/chat /api/ai/models            vLLM 直连（OpenAI 兼容）
      └─ 其余反代 → :8001 平台层（内部 worker，不对外）
```

- **AI 聊天**：Whisker 用 netConnect 直连 [vLLM](https://github.com/vllm-project/vllm)
  （`vllm serve <模型> --port 8002`，端点可在 `native/eonos.conf` 配置，支持远端 GPU 机器）
- **agent 原语**：截图 = GDI BitBlt→BMP；鼠标 = SetCursorPos/mouse_event；键盘 = keybd_event/VkKeyScanA
- **平台层（:8001，可选）**：Python 桌面/工具平台（server.py 等），前门反代目标；
  若只用一个本地模型服务，可不启动它

## 快速开始

```
# 1) 模型服务（可远端；本机示例）
vllm serve <你的模型> --port 8002

# 2) 原生前门（Windows）
native\httpd.exe
#    → [eonos-httpd] 已监听 http://127.0.0.1:8000

# 3) 打开 http://127.0.0.1:8000
#    聊天框支持：screenshot / 截图 · click 100 200 · type: 文本 · run: 命令
```

需要重新构建原生组件时（依赖 Whisker 工具链）：

```powershell
# 需先有 whisker-github 仓库的自举编译器 build\wkcc2.exe（见 native\wk.ps1 头注释）
cd native
powershell -File wk.ps1 inc httpd.wk std\strbuf.inc.wk -o httpd.exe
```

## 目录

| 路径 | 说明 |
|---|---|
| `native/` | **Whisker 原生组件**（httpd.wk 前门、std/ 标准库、wk.ps1 构建驱动、验收脚本） |
| `native/README.md` | 原生组件详述 + Python 退出路线表 |
| `web/` | 前端静态资源（由原生 httpd 读盘直发） |
| `engine/` `core/` `agent/` `mcp/` `compat/` | Python 平台层（旧架构保留，逐步退役） |
| `server.py` `ai_server.py` `__main__.py` | 平台层入口（`python -m eonos --port 8001`） |

## 技术亮点

- **零 libc 前门**：httpd.exe 由 Whisker 自举链产出（PE 自研写出器生成，无 C 运行时）
- **agent 原语全原生**：截屏 7.2MB BMP 完整链路、光标/键盘事件，均为 Whisker 内置函数
- **默认回环绑定**：netListenLocal 让带敏感能力（命令执行、键鼠注入）的服务不暴露局域网
- **AI 无胶水**：聊天请求由 Whisker 直接构造/解析 OpenAI 协议（含 `\uXXXX` 与代理对解码）
