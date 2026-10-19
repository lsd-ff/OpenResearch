# 架构图完整对齐验收

按用户提供的「Sea-Mult-Agent 多智能体科研执行系统」完成独立 Python 重写：保留 React + TypeScript，将 Go + Gin 替换为 FastAPI；Plan Store 使用 SQLite、不可变文件与持久事件日志。图中的可选服务也有独立实现与真实部署验收。

验收日期：2026-10-19。功能代码基线：`3bc4a72`。本表的“通过”表示对应架构能力和所列场景已验证，不表示任意论文的科学效果已复现。具体命令、运行回执与边界见 [本轮验证记录](framework-verification.md)。

| 图中要求 | 实现 | 验收证据 | 状态 |
| --- | --- | --- | --- |
| 聊天、PDF、DAG、执行侧栏、本地会话 | React 工作台，聊天附件直接阅读 PDF；图随侧栏尺寸适配；刷新恢复 | 7 项 Playwright 回归，包含 PDF、图表、下载、刷新、窄屏 | 通过 |
| chat / plan / execute / stream API | FastAPI，兼容图中路由；另提供上传、来源、产物和任务操作 | `test_api.py`、`test_task_commands.py`、真实 HTTP 验收 | 通过 |
| 规则 + LLM 意图识别、会话记忆 | IntentParser、有限历史与来源上下文、SQLite 会话和 revision 冲突检查 | `test_chat_context.py`、`test_conversation_planning.py`；真实模型命名规划 | 通过 |
| LLM DAG 规划 + 模板回退 | PlannerAgent 提出节点、角色、依赖和优先级；服务端校验必要阶段、权限与产物契约；无效图回退并记录原因 | `test_planner_agent.py`；真实模型计划 ce685985351b456fa7328dc16f84bf3d 的 strategy 为 llm | 通过 |
| Plan Store、Artifacts、Events | SQLite WAL、文件 SHA-256、状态/事件/索引事务提交，支持重启恢复 | 存储、并发、迟到结果、产物校验及 GC 行为测试；运行产物持久读取 | 通过 |
| 依赖解析、Ready 队列、并行调度 | 独立 Worker，依赖满足后领取，租约与并发预算由数据库控制 | `test_worker.py`、`test_budgets.py`、`test_process_runtime.py` | 通过 |
| 事件总线 + SSE | 持久日志、终态推送、Last-Event-ID 断线回放 | 真实模型执行收到 18 个事件，终态和游标回放均核对 | 通过 |
| Routed Executor + 四类 Agent | Chat 汇总/问答、Librarian 文献/来源、Data 实际统计/图表、Coder 编码/执行；兼容旧角色 | `test_routed_agents.py`；真实代码计划覆盖 Coder→Chat，数据计划核对实际均值 | 通过 |
| 独立沙箱 API：创建、Python、命令、清理 | Python Sandbox HTTP 服务；API/Worker 经内网调用，仅 Sandbox 挂载 Docker socket | `test_service_http_transport.py`、`test_sandbox_service_docker.py`；最终部署真实 HTTP 执行 | 通过 |
| Docker 默认 + OpenSandbox 回退 | 默认原生 Docker；仅创建失败切换；官方 lifecycle/execd 适配与资源对账 | 真实 opensandbox-server 1.1.1 验收：非 root、原文输出、文件、退出码 7、清理；部署 Sandbox→OpenSandbox 实际输出 45 | 通过 |
| 隔离 Python、临时工作区、代码、日志、图像与产物 | 容器内执行；自动收集 /workspace/artifacts；类型、哈希、归属检查；持久预览与下载 | `test_output_collection.py`、`test_output_downloads.py`；网页显示实际 SVG 并下载 demo.svg | 通过 |
| OpenAI 兼容模型 | 统一结构化模型契约，支持 OpenAI-compatible 与 Anthropic | OpenAI/Anthropic 传输协议测试；用户配置的 Anthropic/DeepSeek 真实调用通过 | 通过 |
| HuggingFace Papers / Papers with Code 仓库发现 | 沿用原 Go 的 HF Papers 来源，验证真实 githubRepo 并保留论文链接 | `test_discovery_providers.py`；线上 DeepSeek-V3 返回实际 deepseek-ai/deepseek-v3 与 arXiv 2412.19437 | 通过 |
| GitHub Search 回退 | 主来源空结果或失败后检索 GitHub / arXiv，记录 fallback_used | 协议回退测试；线上 Attention Is All You Need 返回 GitHub 候选且 fallback_used=true | 通过 |
| 可选独立 Python AI 意图服务 | 独立 intent 命令、/v1/intent、Compose ai profile、单独凭据、有界上下文和同预算回退 | `test_intent_service.py`、真实 HTTP 边界测试；API 容器调用独立服务分类为 data | 通过 |
| 端到端：请求→产物→SSE | 真实模型构图，Worker 经 HTTP 沙箱运行，再由 Agent 汇总；真实数据统计与图表 | `scripts/verify_framework.py` 最终部署回执为 passed，stdout=42、3 行数据实际 mean=2 | 通过 |

该基线验收中，四个部署进程的 151 个 Python 源文件 SHA-256 均与工作区一致。后续 Transformer 已完成真实代码与训练的完整复跑，新增大型依赖、原生扩展、执行报告和结果链接验收，见 [最新实测报告](transformer-reproduction.md)。密钥、回退服务配置与验收数据保存在忽略路径。遵循 [code_skill](../.trae/skills/code_skill/SKILL.md)，每批修改通过检查后提交并推送。
