# OpenResearch Python

AGI-OpenResearch 的独立 Python 重写。React 工作台、FastAPI、模型 DAG Planner、持久 Worker、Chat/Librarian/Data/Coder 四类 Agent 和独立沙箱服务组成科研执行链。支持自然语言规划、文献检索、数据分析、代码执行、图表下载，以及固定源码与依赖后的实验、消融和证据验收。逐项对照用户架构图见 [框架对齐验收](docs/framework-parity.md)。

遵循项目 [code_skill](.trae/skills/code_skill/SKILL.md)：职责拆分、参数对象、中文关键注释、事务并发控制和小步行为测试。原 Go 工程保持独立。

## 启动

需要 Python 3.12–3.14、uv、Node.js 22 和 Docker Engine / Docker Desktop Linux 容器。显式代码与示例实验无需模型密钥。在本目录运行：

```powershell
uv sync --locked
Copy-Item .env.example .env
uv run --locked openresearch doctor
uv run --locked openresearch db upgrade
```

在 frontend 目录运行 `npm ci`、`npm run build`，回到本目录，在两个终端分别运行：

```powershell
uv run --locked openresearch serve
uv run --locked openresearch worker
```

工作台：http://127.0.0.1:8080/；接口文档：http://127.0.0.1:8080/docs。

也可在本目录启动容器部署，镜像会构建工作台：

```powershell
docker compose up -d --build
docker compose logs -f worker
```

默认启动 API、Worker、Sandbox 三个服务，仅发布本机 8080 端口，OPENRESEARCH_PORT 可调整。API 与 Worker 共享 research-data 卷；只有 Sandbox 服务挂载 Docker socket。使用 `docker compose down` 停止，数据卷保留。

可选独立意图服务通过 `docker compose --profile ai up -d --build` 启动，并设置 `OPENRESEARCH_INTENT_SERVICE_URL=http://intent:8083`；不可用时在同一识别预算内回退本地模型。OpenSandbox 回退通过服务地址、凭据和启用开关配置。详见 [意图服务](docs/intent-service.md) 与 [沙箱服务](docs/sandbox-service.md)。

## 使用

- 直接在聊天中说出论文或方法名称，例如「复现一下 Transformer 论文的核心架构并跑通测试」。系统先检索来源；模型查询无结果或失败时，会用用户给出的名称再检索一次。匹配明确则自动选取实现并生成流程图，真正歧义才列出候选。也可直接提供仓库链接，例如「用 https://github.com/pallets/itsdangerous 做签名校验冒烟实验」。
- 添加 PDF 后发送「分析这篇论文」或「复现这篇论文」。解析在 Docker 中完成；分析引用核对实际页码，复现会从原文或真实检索结果发现仓库。多个候选、缺失源码等材料会在聊天中追问，直接回复编号或链接继续。
- 添加 CSV、TSV、JSONL 或 JSON 对象数组，描述评测目标。常见标签列可识别，歧义列会追问；无需重新上传。标签留在评估端，实验只接收特征。见 [实验协议](docs/experiments.md) 与 [examples](examples/)。
- 发送「对比 LangChain 与 LlamaIndex 的 RAG 性能」可创建独立框架分支，共用数据与嵌入协议，实际安装并调用框架，再用相同的真实模型生成回答。未上传数据时明确使用合成功能样例；发送「比较 LangChain 与 LlamaIndex 的适用场景」则只分析实际发布资料。默认自动执行，仍遵守「只规划」及审批设置。范围与实测见 [框架对比](docs/framework-comparison.md)。
- 上传数据后也可直接要求统计和缺失值分析；Data Agent 计算实际数据并生成 SVG。代码把文件写入 `/workspace/artifacts` 后会自动收集、校验和保存，支持图像预览与下载，详见 [输出产物](docs/output-artifacts.md)。PDF 可在聊天附件旁直接阅读。
- 「高级实验设置」是可选入口，用于手动指定仓库、入口、固定依赖、列映射和验收阈值。普通聊天实验无需填写该表单。
- 选择节点查看日志、报告与证据图。支持整图、单节点执行；API 提供角色重分配、审批、取消、失败节点重试和 SSE 游标回放。
- 普通聊天、自然语言代码生成、自动补丁修复和语义主张评估需要配置 OpenAI-compatible 或 Anthropic Messages 模型。未配置或响应无效会明确失败或标记不可验证；配置和真实联调见 [模型说明](docs/models.md)。
- 普通代码支持有界自动恢复：明确说「直接执行以下代码并修复错误」时自动入队；语法和可恢复的 API/导入错误可由模型提出修复，缺失公开模块经版本与 wheel 哈希校验后离线安装，再使用新容器运行。原代码、错误和修复记录保留，故意失败的断言不会被改成成功。`OPENRESEARCH_CODE_MAX_REPAIRS=0` 可关闭，准备和执行阶段分别最多 2 次修复。详见 [普通代码恢复](docs/code-recovery.md)。
- 科研和框架计划支持初始准备及执行阶段的有界环境恢复、依赖删除/替换，保存实际错误、来源证据、每次采用的完整锁；失败也能回传聊天报告。Benchmark 代码或环境变更后必须重新通过独立样本预检，用户固定版本、指定库与禁用指令继续生效。自动运行时切换仍待补齐，详见 [环境恢复](docs/environment-recovery.md)。

示例部署验收会真实执行沙箱程序，重算基线与消融准确率并下载产物：

```powershell
uv run --locked python scripts/verify_deployment.py
```

示例数据为四行协议验收数据，其结果不代表论文复现效果。

单句自然对话已实际验收：空白会话只发送「复现一下 Transformer 论文的核心架构并跑通测试」，系统自动固定真实完整实现、运行原仓库 pytest 套件并将中文报告返回聊天。最终 **8/8 节点完成，18 项测试一次通过（3.95 秒），退出码 0，3/3 功能规则通过**，没有提供脚本、填写高级配置或手动重试。来源、执行回执、工程检查与局限见 [Transformer 对话验收报告](docs/transformer-chat-acceptance.md)。本次未执行训练或 WMT/BLEU 评测。

另有 [提供完整脚本后的 Transformer 复跑记录](docs/transformer-reproduction.md)：包含原论文解析、数值/梯度检查及 copy/reverse 小规模训练，可运行 `uv run --locked python scripts/verify_transformer.py`。该历史记录不作为单句自动规划与测试的验收证据。

## 配置与边界

唯一配置入口是 src/openresearch/config.py，环境变量统一使用 OPENRESEARCH_。API 与 Worker 必须使用相同数据目录和沙箱配置。

OPENRESEARCH_API_TOKEN 非空时要求 Bearer token；「连接设置」可换取 HttpOnly Cookie，供 REST、SSE、PDF 与产物读取使用。用户 ID 是可信本地客户端声明的资源归属标识，没有独立账号认证；当前面向单机可信使用者。

原生 Docker 默认：1 CPU、1 GiB、64 进程、非 root、只读根目录、禁止联网。/tmp 为 256 MiB 不可执行内存盘；/workspace 为 1 GiB 内存盘，支持 wheel 扩展模块。wheel 下载总量默认最多 128 MiB。任务 600 秒、计划 2400 秒、最多 100 次节点尝试；研究修复最多 2 次、消融最多 6 组，自动 ToT 默认选择最多 3 组。默认预算覆盖首次多框架下载；已有 `.env` 的显式配置优先，不能被规划器自行突破。当前用于小型 CPU 实验，未交付 GPU 训练调度。OpenSandbox 使用其服务端隔离配置，客户端请求资源上限、非 root 命令和默认拒绝的出站策略。

仓库仅支持 HTTPS GitHub；分支在获取时解析为完整提交 SHA，拒绝链接、特殊文件及越界归档。依赖最终必须完整 name==version 固定，限定 PyPI 上适配 CPython 3.12 / Linux x86_64 的 wheel，校验哈希后在容器中离线安装。自动规划先将模型建议的版本范围解析成固定版本；不执行源码包构建或仓库安装脚本。

目前没有 OCR，扫描件保留缺失文本状态。检索优先使用 HuggingFace Papers（原 Go 中的 Papers with Code 来源），空结果或失败时回退 GitHub / arXiv；支持单独指定检索来源，并保留论文出处。公开检索和 arXiv PDF 下载支持环境代理或 OPENRESEARCH_SOURCE_PROXY_URL；不把代理设置或模型凭据送入实验容器。语义判断是受证据引用约束的辅助评估，数值验收由服务端重算。成功运行、空验收规则和示例正确率均不能自动等同于论文主张已复现。

独立运行时可选择 docker、http、opensandbox 或 auto 后端。http 既支持本项目 Python 服务，也兼容原 Go NDJSON 协议；仅旧 Go 服务缺少清单和镜像摘要。Python HTTP 服务可对账容器并返回原生 Docker 的实际镜像摘要。

管理员可注册命名运行时，基础层已验证目标 Python 3.11/3.12/3.13 的依赖解析和实际安装，以及真实 GPU 容器中的 CUDA 向量计算。自动计划选择、版本故障后的自动切换及 GPU/full 资源调度尚未接完；默认科研流程仍使用 Python 3.12 CPU。配置契约、真实回执与限制见 [运行时基础](docs/runtime-profiles.md)。

## 工程与恢复

```powershell
uv run --locked python scripts/check.py
$env:OPENRESEARCH_TEST_DOCKER = '1'
uv run --locked pytest -m docker
# 另启用真实 GitHub / PyPI 验收
$env:OPENRESEARCH_TEST_EXTERNAL = '1'
uv run --locked pytest -m docker
```

默认门禁包含 Ruff、严格 mypy、离线行为测试及 sdist/wheel 构建；前端运行 npm run lint 和 npm run build。CI 已配置 Windows/Linux 与 Python 3.12/3.13，远程结果以 GitHub Actions 实际运行记录为准。

CI 的 browser 作业在独立 Linux 环境运行 Chromium、API 和真实 Docker Worker，覆盖 PDF 阅读、图表输出与下载、数据分析、聊天追问、刷新恢复、名称检索和窄屏操作。分类、候选选择、名称检索和 JSON API 修复提议使用受控夹具，不提供实验结果；程序、图表和统计结果来自实际执行。真实模型 DAG、HTTP 沙箱、数据与 SSE 可使用 `scripts/verify_framework.py` 另行验收；自动设计、源码复现和 Benchmark 由 `scripts/verify_auto_planning.py` 验收。网页测试使用独立端口、数据目录和测试凭据，截图与失败 trace 作为 CI 产物保留 7 天。

SQLite 使用 WAL、外键与写事务锁。状态、事件和产物索引同事务提交；上传、会话、来源页和历史尝试持久化，迁移包含 0001_runtime 与 0002_documents_context。产物读取验证 SHA-256。租约过期恢复会拒绝旧结果；实验允许重复执行，不承诺 exactly-once。

Worker 按数据卷的持久 namespace 对账孤立容器，并清理超宽限期的未引用产物；有效尝试和历史索引仍引用的文件保留。源码及 wheel 缓存按内容摘要复用，目前保留至管理员清理数据目录。

详见 [架构](docs/architecture.md)、[HTTP 契约](docs/api.md)、[阶段验收](docs/implementation-plan.md)、[本轮架构验证](docs/framework-verification.md)、[历史验证记录](docs/verification.md) 和 [贡献规范](CONTRIBUTING.md)。
