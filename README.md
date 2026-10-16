# OpenResearch Python

AGI-OpenResearch 的独立 Python 重写。API、Worker、SQLite、Docker 沙箱与 React 工作台组成可运行的实验链：上传论文 → 固定源码和依赖 → 冻结验收规则 → 受限代码补丁 → 实验与消融 → 预测校验、指标重算和证据报告。

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

默认仅发布本机 8080 端口，OPENRESEARCH_PORT 可调整。API 和 Worker 共享 research-data 卷与 Docker socket；使用 `docker compose down` 停止，数据卷保留。Docker socket 仅挂载到可信管理进程，实验容器不挂载它。

## 使用

- 直接在聊天中描述目标，例如「用 https://github.com/pallets/itsdangerous 做签名校验冒烟实验」。系统核验来源、自动设计入口和依赖、生成流程图，点击运行即可。
- 添加 PDF 后发送「分析这篇论文」或「复现这篇论文」。解析在 Docker 中完成；分析引用核对实际页码，复现会从原文或真实检索结果发现仓库。多个候选、缺失源码等材料会在聊天中追问，直接回复编号或链接继续。
- 添加 CSV、TSV、JSONL 或 JSON 对象数组，描述评测目标。常见标签列可识别，歧义列会追问；无需重新上传。标签留在评估端，实验只接收特征。见 [实验协议](docs/experiments.md) 与 [examples](examples/)。
- 「高级实验设置」是可选入口，用于手动指定仓库、入口、固定依赖、列映射和验收阈值。普通聊天实验无需填写该表单。
- 选择节点查看日志、报告与证据图。支持整图、单节点执行；API 提供角色重分配、审批、取消、失败节点重试和 SSE 游标回放。
- 普通聊天、自然语言代码生成、自动补丁修复和语义主张评估需要配置 OpenAI-compatible 或 Anthropic Messages 模型。未配置或响应无效会明确失败或标记不可验证；配置和真实联调见 [模型说明](docs/models.md)。

示例部署验收会真实执行沙箱程序，重算基线与消融准确率并下载产物：

```powershell
uv run --locked python scripts/verify_deployment.py
```

示例数据为四行协议验收数据，其结果不代表论文复现效果。

## 配置与边界

唯一配置入口是 src/openresearch/config.py，环境变量统一使用 OPENRESEARCH_。API 与 Worker 必须使用相同数据目录和沙箱配置。

OPENRESEARCH_API_TOKEN 非空时要求 Bearer token；「连接设置」可换取 HttpOnly Cookie，供 REST、SSE、PDF 与产物读取使用。用户 ID 是可信本地客户端声明的资源归属标识，没有独立账号认证；当前面向单机可信使用者。

默认沙箱：1 CPU、256 MiB、64 进程、非 root、只读根目录、禁止联网。/tmp 为 64 MiB 不可执行内存盘；/workspace 为 256 MiB 内存盘，支持 wheel 扩展模块。任务 120 秒、计划 600 秒、最多 10 次节点尝试；研究修复最多 2 次、消融最多 3 组。部分上限可配置。当前用于小型 CPU 实验，未交付 GPU 训练调度。

仓库仅支持 HTTPS GitHub；分支在获取时解析为完整提交 SHA，拒绝链接、特殊文件及越界归档。依赖最终必须完整 name==version 固定，限定 PyPI 上适配 CPython 3.12 / Linux x86_64 的 wheel，校验哈希后在容器中离线安装。自动规划先将模型建议的版本范围解析成固定版本；不执行源码包构建或仓库安装脚本。

目前没有 OCR，扫描件保留缺失文本状态。检索支持 GitHub 与 arXiv，外部不可用时明确报错，可改用直接上传和显式仓库。语义判断是受证据引用约束的辅助评估，数值验收由服务端重算。成功运行、空验收规则和示例正确率均不能自动等同于论文主张已复现。

可选 OPENRESEARCH_SANDBOX_BACKEND=http 接入原 Go 沙箱协议；该协议不提供容器清单和镜像摘要，完整对账与版本记录优先使用原生 Docker 后端。

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

SQLite 使用 WAL、外键与写事务锁。状态、事件和产物索引同事务提交；上传、会话、来源页和历史尝试持久化，迁移包含 0001_runtime 与 0002_documents_context。产物读取验证 SHA-256。租约过期恢复会拒绝旧结果；实验允许重复执行，不承诺 exactly-once。

Worker 按数据卷的持久 namespace 对账孤立容器，并清理超宽限期的未引用产物；有效尝试和历史索引仍引用的文件保留。源码及 wheel 缓存按内容摘要复用，目前保留至管理员清理数据目录。

详见 [架构](docs/architecture.md)、[HTTP 契约](docs/api.md)、[阶段验收](docs/implementation-plan.md)、[验证记录](docs/verification.md) 和 [贡献规范](CONTRIBUTING.md)。
