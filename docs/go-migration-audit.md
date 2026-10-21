# Go → Python 源码迁移复核

复核基线为只读 Go `50348231852d7dac36b52f643d59a3f2af59d20a`，Python 为 `codex/go-feature-parity`。本表对照实际注册接口、执行分支和可观察行为；历史专项通过不代替最新整合验收。完整执行记录见 [迁移计划](implementation-plan.md)，请求约束见 [旧接口兼容](legacy-api.md)。

| Go 实际入口 / 实现 | Python 对应实现 | 行为验证与差异 |
| --- | --- | --- |
| `cmd/app/main.go`、`internal/api/routes.go` 的 ping、hello、health、chat、pdf-proxy、execute、plan | `api/system.py`、`conversations.py`、`sources.py`、`tasks.py`、`plans.py` | 原路径保留；hello 纯文本。本轮补齐 hello 并验证认证 Cookie；PDF 支持经公共地址校验的 URL |
| `internal/api/plan_runtime.go` 的计划读取、事件、执行、取消、审批、节点 retry/reassign、stream | `api/plans.py`、任务及事件路由、`storage/task_commands.py` | 持久状态、所属身份、审批、累计预算和断线回放；见 API、task_commands、stream 行为测试 |
| `internal/api/uploads.go` 的上传与内容读取 | `api/uploads.py`、`services/uploads.py` | 保留路径，增加所属身份、不可变 SHA 和体积限制；实际 PDF 容器解析与下载已有验收 |
| `internal/Intent` 的规则/LLM、最近会话；可选 Redis | `services/intent.py`、`chat_context.py`、SQLite 会话 | 持久历史及冲突校验；不要求 Redis，属于存储选型变化 |
| `planner` 的 Agent DAG 与模板 | `planner/agent.py` 及工作流模板 | 节点、边、角色及必要阶段校验；非法模型图记录后模板回退；不接受模型扩大执行权限 |
| `scheduler` 的 Ready、并行、依赖、预算、审批、恢复 | 独立 Worker、Repository 事务、租约与取消 | SQLite WAL 取代内存/可选 JSON 文件；多进程领取及迟到结果拒绝已有真实测试 |
| Librarian、Data、Chat Agent | 对应 `agents`、来源检索、PDF、统计、主张和汇总 | 实际来源、页码及执行证据；模型生成的分析不等于实验成功 |
| Coder 的生成、运行时、依赖安装及删除/替换/升级恢复 | `agents/environment.py`、`code_execute.py`、`research/dependency_recovery.py`、`runtime_recovery.py` | 固定公开 wheel、离线容器安装、有界恢复；不执行任意宿主路径或安装脚本 |
| ResearchCoding 的数据概况、adapter、独立预检、正式执行、验证、修复 | `research`、相关 Agent 及 `legacy_stages.py` | 最多 8 条真实预检、独立容器、实际预测重算及累计修复预算；原始失败回执保留 |
| framework 分支、ToT、论文主张/证据、结果对比/修复/绘图、通用研究 | `legacy_frameworks.py`、`legacy_reviews.py`、`legacy_gaps.py`、`legacy_knowledge.py` | 所有原任务类别映射到受管阶段；阶段完成与整图/科学主张达标分别报告，字段详见兼容文档 |
| `sandboxserver` 的 `/api/v1` health/create/delete/python/commands 及两类 stream | `sandbox/service.py` 及 HTTP 适配器 | 七个原接口保留；Docker、OpenSandbox、文件/产物、取消/回收与 GPU 专项已实跑 |
| 独立 docker-core 的 TTY、Commit/回滚、Watchdog、候选竞速与配额 | `sandbox/core` | CPU/GPU 实际测试已有证据；作为独立库提供，不新增聊天任意 shell 入口；GPU 不快照显存 |
| React 工作台、PDF、DAG、侧栏、SSE、会话 | 独立 `frontend` | 上传阅读、自动规划、真实运行结果、刷新恢复、窄屏及断线用例；最新完整 UI 回归待统一验收 |
| 单文件 Go app | Python 一体化 app 与 PyInstaller 发行物 | 内嵌前端/运行资源/uv，实验仍进 Docker；最新代码的单文件重建与独立目录验收待完成 |
| 可选旧 AI 服务的 `/predict`、`/health` | `api/intent_service.py` | 本轮补齐旧协议；实际分类器取代旧关键字 mock；置信度 0 表示未提供校准概率，模型失败返回 503 |

Go 的 `tools/ask_user_question` 是独立工具实现，没有在该基线的主 API 注册问题/回答路由，也没有被生产 Agent 引用；Python 主工作流的澄清使用持久会话和 `/api/messages`。不把未接入工具、注释中的 BERT、docker-core README 中尚未部署的 gVisor/nvproxy/WebSocket 设想算作已运行的 Go 产品功能。Python 也不声称已部署这些设想。

本轮发现的三个接口缺口已经补齐。仍需完成最新 Docker 整体回归、发行物、真实模型的新会话 Transformer 网页验收及统一部署，才可交付最终结果。GitHub Actions 因账号限制未启动，用户已选择先完成本地验证；本地通过与最新远端 CI 通过分开记录。
