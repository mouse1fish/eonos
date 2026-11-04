# EonOS 原生组件（Whisker）

EonOS 的系统层组件，**全部用 Whisker 语言编写、自举编译器构建、零 libc 依赖**。
Python 仅保留 AI 推理层（`ai_server.py`），其余能力随 EonOS 演进逐步原生化。

## 架构

```
浏览器 / 客户端
      │
      ▼
:8000  httpd.exe（Whisker 原生前门·网关模式，仅绑定 127.0.0.1 —— 唯一对外监听者）
      ├─ 原生路由（Whisker 直答，Python 不参与）：
      │    GET  /                    → 读盘发首页（web/index.html）
      │    GET  /ui/*                → 静态文件（MIME 判断 + 路径穿越防护）
      │    GET  /api/frontdoor · /api/time · /native · /api/ai/models
      │         /api/agent/status（屏幕/光标）· /api/shot.bmp（截图大图 8MB 缓冲）
      │    POST /api/echo · POST /api/exec（命令执行器，procSpawnPipe，上限 64KB）
      │    POST /api/ai/chat → Whisker 直连 vLLM（OpenAI 兼容协议，原生客户端）
      │    POST /api/screenshot · /api/mouse · /api/type · /api/command（agent 原语，全原生）
      └─ 其余全部反代 → :8001 后端（内部 worker，不对外）
                          /api/status · /api/chat(AI) · /api/tools · agent 能力
```

**AI 聊天走原生 vLLM 直连**（不经任何我们自己的 Python 胶水）：
`eonos.conf` 配 `vllm_host` / `vllm_port`（默认 127.0.0.1:8002，可指向远端 GPU 机器）。
启动 vLLM：`vllm serve <模型> --port 8002`。客户端自动发现模型（/v1/models），
请求/响应全程 JSON（含 `\uXXXX` 与代理对解码）。验收替身：`test/mock_vllm.py`。

后端（平台 UI 反代目标）起法二选一：
- 全平台：`python -m eonos --host 127.0.0.1 --port 8001`（网关默认后端，UI+AI+工具）
- 精简 AI 层：`python -m eonos.ai_server`（只有 /api/ai/health 与 /api/ai/chat）

AI 层/平台离线时，网关对反代路径优雅降级 502 `{"error":"ai layer offline"}`。

启动顺序：先 Python 后端（:8001），再 `httpd.exe`（:8000）。

## 组件清单

| 文件 | 说明 |
|---|---|
| `httpd.wk` | 原生 HTTP 服务器（Acceptor + 每连接 Conn actor；JSON/exec/反代路由） |
| `ai_server.py` | Python AI 推理层（内部 :8001，仅回环） |
| `wk.ps1` | 构建驱动（build/run/test/inc 四动词，封装自举编译器调用） |
| `std/strbuf.inc.wk` | 标准库·动态字符串（句柄式，倍增扩容；def 片段拼接使用） |
| `std/strbuf_test.wk` | strbuf 验收用例 |
| `../web/index.html` | 平台首页（静态，Whisker 读盘直发；旧 Python 生成页的快照） |
| `exec_test.wk` / `body*.json` | exec 路由的隔离测试与请求体样例 |

## 构建

```powershell
# httpd（拼接 strbuf 片段）
powershell -File wk.ps1 inc httpd.wk std\strbuf.inc.wk -o httpd.exe

# 运行测试
powershell -File wk.ps1 test std\strbuf_test.wk std\strbuf.inc.wk
```

依赖 Whisker 自举编译器：`G:\trea-Eon-os\trea-xiancheng-bianyiqi\build\wkcc2.exe`
（其维护流程见该项目的 `交接文档-自举完成.md`）。

## 门面特性

- **零 libc**：PE 由自研写出器直接生成，无 C 运行时、无链接器参与
- **逐字节自举**：编译器编译自身，产物与自身逐字节一致（fixpoint）
- **默认回环绑定**：netListenLocal 让带敏感能力（如命令执行）的服务不暴露局域网
- **Actor 并发**：每连接一个 actor，阻塞原语天然并发，无需回调地狱

## 安全说明

- `POST /api/exec` 执行任意命令——设计即如此（本地 AI OS 平台）。缓解：
  仅回环监听、输出上限 64KB；**不要把 8000 端口转发到外部网络**
- 命令无超时机制（procWait 为无限等待）；子进程挂起需手动清理
- 缓冲区走 bump 分配器不回收，按连接/任务生命周期一次性消耗

## Python 的角色与退出路线（重要澄清）

**Whisker 编译器链零 Python**（自举 fixpoint 逐字节一致，日常构建仅用自举产物）。
EonOS 中 Python 仅剩两个职责，且**都有明确退出路径**：

| Python 现状职责 | 为什么现在还在 | 退出路径 |
|---|---|---|
| ~~AI 模型运行时（engine 推理）~~ | **已退役**（2026-11-03）：聊天改由 Whisker 直连 vLLM | ✓ 完成——`/api/ai/chat` 原生实现，UI 聊天已切到该端点 |
| ~~桌面 agent（截图/鼠标/键盘）~~ | **已退役**（2026-11-03）：GDI/user32 原语落地 | ✓ 完成——screenshot/mouseMove/click/keyType 全原生（见下） |

**已完成的原生化**（每杀一次 Python，这张表就长一行）：
首页与静态资源 ✓、AI 聊天（直连 vLLM）✓、**agent（截图/鼠标/键盘）✓**、
/api/frontdoor ✓、/api/exec ✓、/api/command ✓、/api/time ✓、/api/echo ✓、
/api/ai/models ✓、/api/agent/status ✓、网关反代与 502 降级 ✓。

**agent 原语**（runtime user32.dll + gdi32.dll 导入，Whisker 内置）：
`screenW/screenH`（屏幕尺寸）、`mouseX/mouseY`（光标）、`mouseMove/mouseDown/mouseUp`（1 左 2 右 3 中）、
`keyDown/keyUp`（虚拟键码）、`keyVk`（字符→VK，高字节为 Shift 状态）、`screenshot(path)`（GDI 抓屏写 BMP）。
实测：截屏 7,285,530 字节 = 54 + 1707×1067×4 分毫不差；光标移动/复位、VK 映射、命令执行全通。

> 现在用 AI 能力时，链路是：浏览器 → Whisker httpd → vLLM（现成模型服务）。
> **我们自己的 Python 代码已不在任何用户路径上**——剩余的 Python 只可能是"模型服务本身"（vLLM）。

## std 路线图（core / extended 分层）

**core**（随组件需求推进）：strbuf ✓ → json → net → time → fs
**extended**（待原语补齐）：桌面 agent 相关（截图/鼠标/键盘，需 GDI/SendInput 原语）

语言层候选（Hare 调研采纳项）：defer、use/include 机制、haredoc、错误约定（strerror 注册表）。
