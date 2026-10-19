# 架构图完整对齐验收

目标：按用户提供的「Sea-Mult-Agent 多智能体科研执行系统」实现 Python 重写，保留 React + TypeScript 前端，将 Go + Gin 换为 Python 服务。图中的可选模块也应有可配置、可验证的实现。已有持久化与恢复能力继续保留。

以下为本轮逐项验收清单；未有对应证据的项目不标记完成。离线协议测试与外部真实服务验收分别记录。

| 图中要求 | 当前实现 / 本轮工作 | 验收状态 |
| --- | --- | --- |
| 聊天、PDF、DAG、执行侧栏、本地会话 | React 工作台已有，需要最终网页回归 | 待最终验收 |
| chat / plan / execute / stream API | FastAPI 已有，需要逐项核对路由兼容 | 待验收 |
| 规则 + LLM 意图识别、会话记忆 | IntentParser、SQLite 会话已有 | 待最终验收 |
| LLM DAG 规划 + 模板回退 | 当前只有模板图和模型实验设计，需要补齐模型 DAG 规划 | 待实现 |
| Plan Store、Artifacts、Events | SQLite 与文件存储已有，优于原内存存储的持久性 | 待最终验收 |
| 依赖解析、Ready 队列、并行调度 | Worker 租约调度已有 | 待最终验收 |
| 事件总线 + SSE | 持久事件日志与可恢复 SSE 已有 | 待最终验收 |
| Routed Executor + Chat/Librarian/Data/Coder | 目前按任务类型路由，需补齐四类角色和数据任务 | 待实现 |
| 独立沙箱 API：创建、Python、命令、清理 | 原生 Docker 与 Go HTTP 客户端已有，需独立 Python 服务及命令接口 | 待实现 |
| Docker 默认 + OpenSandbox 回退 | Docker 已有；回退需实现并验收 | 待实现 |
| 容器内 Python、临时工作区、代码、日志、图像和产物 | 基础执行已有，需核对图像产物与下载展示 | 待验收 |
| OpenAI 兼容模型 | 已有 OpenAI / Anthropic，实际配置使用 Anthropic | 待最终验收 |
| HuggingFace Papers / Papers with Code 仓库发现 | 按原 Go 实现接 HF Papers，失败或无仓库时回退 GitHub；保留论文出处 | 实现及协议测试中 |
| GitHub Search 回退 | 已有，补充主来源失败/空结果的自动回退 | 实现及协议测试中 |
| 可选独立 Python AI 意图识别服务 | 当前在 API 内，需要可选服务入口及回退 | 待实现 |
| 端到端：请求到产物再到 SSE | 需要对上述新增能力做完整网页、HTTP、Docker 和真实模型验收 | 待最终验收 |

工程门禁：遵循 code_skill；Ruff、严格 mypy、行为测试、前端构建与 E2E；真实代码只在沙箱执行；每批通过后提交并推送 GitHub，不提交密钥和运行数据。
