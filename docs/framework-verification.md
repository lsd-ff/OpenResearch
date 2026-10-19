# 架构补齐验证记录

日期：2026-10-19。功能代码提交：`3bc4a729cb89c77a9586814c122f97512f58a7e4`。这是本轮架构补齐的最终验证；[早期验证记录](verification.md) 保留原阶段结果，不用旧计数覆盖本轮结果。逐项能力对应见 [框架对齐表](framework-parity.md)。

## 工程与真实执行

| 检查 | 实际结果 |
| --- | --- |
| `uv run --locked python scripts/check.py` | Ruff、格式、严格 mypy 通过；151 个源文件；231 项工程测试通过、19 项真实执行测试不在默认门禁运行；sdist/wheel 构建成功 |
| 同时启用 TEST_DOCKER、TEST_EXTERNAL、TEST_OPENSANDBOX，运行 `pytest -m docker -q` | 19 通过、231 未选中，无跳过；实际使用 Docker、GitHub、PyPI 和 OpenSandbox |
| 前端 `npm run lint`、`npm run build` | ESLint、TypeScript、Vite 通过 |
| 前端 `npm run test:e2e` | 7 通过；Chromium 覆盖 PDF 原文、SVG 预览/下载、数据图表、聊天追问、刷新恢复、DAG 自适配与窄屏 |
| GitHub Actions 功能代码基线 | [push 运行](https://github.com/lsd-ff/OpenResearch-Python/actions/runs/39192442530)、[PR 运行](https://github.com/lsd-ff/OpenResearch-Python/actions/runs/39192447013) 均成功；Windows/Ubuntu、Python 3.12/3.13、Docker、前端和网页门禁通过 |

真实外部验收明确选择后才运行：

```powershell
uv run --locked python scripts/check.py
$env:OPENRESEARCH_TEST_DOCKER = '1'
$env:OPENRESEARCH_TEST_EXTERNAL = '1'
$env:OPENRESEARCH_TEST_OPENSANDBOX = '1'
uv run --locked pytest -m docker -q
```

OpenSandbox 用例还需要可达的真实服务及本地配置，默认测试不会代替部署者创建第三方服务。网页分类、候选选择与搜索使用测试夹具；代码、图表、统计与程序输出来自实际执行。真实模型、真实公共检索由下述独立验收证明。

## 最终部署与模型链

`docker compose --profile ai up -d --build` 启动 API、Worker、Sandbox、Intent。API、Sandbox、Intent 健康检查通过，Worker 正常运行。四个进程的 151 个 Python 文件逐个 SHA-256 与工作区一致。Docker 挂载检查确认仅 Sandbox 持有 `/var/run/docker.sock`；API/Worker 仅挂载数据卷。

使用用户配置的 Anthropic 协议与 DeepSeek-V4-Flash 模型，运行 `uv run --locked python scripts/verify_framework.py`。最终部署回执 `.data/framework-acceptance/6c1080e443cb4798aaf0957e61dc3cb3.json` 为 passed：

- 代码计划 `ce685985351b456fa7328dc16f84bf3d` 由真实模型规划，strategy=llm；角色为 Coder、Coder、Chat。Worker 通过独立 HTTP 沙箱实际输出 `42\n`，保存 generated_code、run_metrics、execution_files、chat_answer。读取 18 个事件，并核对终态 SSE 和 Last-Event-ID 回放。
- 数据计划 `3baf956771d34725a07f72cc36b20871` 分析实际上传的 3 行数据，均值为 2，上传摘要匹配；图表反映实际 1/3 缺失比例。
- API 容器通过 RemoteIntentClient 调用独立 Intent 服务，真实返回 workflow=data。
- 最终容器 Sandbox→宿主 OpenSandbox 服务，实际创建、执行 `print(9 * 5)` 得到 `45\n`、退出码 0，并删除沙箱；不是仅探测端口。

`scripts/verify_framework.py` 会调用真实模型并实际执行沙箱代码；默认工程测试不消耗供应商调用。

## 公开来源与命名规划

线上 HuggingFace Papers 查询 DeepSeek-V3 返回实际仓库 `https://github.com/deepseek-ai/deepseek-v3` 和论文 arXiv 2412.19437。自动查询 Attention Is All You Need 时回退 GitHub，记录 source=github、fallback_used=true，并返回 jadore801120、lsdefine、hkproj 等实际候选。公开来源使用部署环境的代理设置，凭据不进入实验容器。

`/api/pdf-proxy?url=https://arxiv.org/abs/1706.03762` 返回 HTTP 200、2,215,244 字节，文件以 `%PDF` 开头；这是实际 PDF 下载，不代表 arXiv 元数据 API 永久可用。

对用户原话「复现一下 Transformer 论文的核心架构并跑通测试」，真实 `/api/messages` 返回 200、type=plan，生成 8 节点模型 DAG `565a3aa4e20a46aeb994a2c497386436`，选取实际检索仓库 `https://github.com/kamarain/transformer_intro`，clarification=null。此次仅验收无需手填来源的发现与规划，没有运行该 Transformer 实验，也没有宣称论文效果已复现。

## OpenSandbox 发布版兼容

本机单独安装 opensandbox-server 1.1.1，与应用依赖环境隔离；execd 镜像为 v1.1.0。真实验收发现其命令入口接受字符串，流虽标记 text/event-stream，却可能使用逐行裸 JSON，且 stdout/stderr 事件丢失换行、非零退出缺少 execution_complete。适配器已实现参数逐项引用、两种流格式解析、容器内原始字节帧和终态状态查询；非零退出据实际 `/command/status` 判定，不伪造成功完成事件。

`test_opensandbox_live.py` 只在测试中注入主后端“创建失败”，随后实际使用回退服务，验证 UID=65534、stdout/stderr 精确保留空行/Unicode/末尾无换行、文件往返、退出码 7、清单和删除完成。已经开始执行的任务不会切换后端重跑。

本机回退服务的强凭据与配置在忽略的 `.env`、`.data/opensandbox-verification.toml` 中。应用四个容器和回退服务保留运行，网页测试使用的辅助进程在验收后停止；启动详情见 [沙箱服务](sandbox-service.md)。

## 结果范围

所有成功结论限定于上面的实际验证。OpenAI-compatible 使用传输协议测试，用户的 Anthropic 配置经过真实调用；没有使用另一套真实 OpenAI 账号凭据作推断。模型 DAG 受支持能力与服务端契约约束，必要阶段和权限不能由模型移除。当前支持本机小型 CPU 实验；GPU 训练、OCR、跨主机 Worker 和任意源码包构建不在架构图本次交付范围。

本轮未把测试夹具当作科研结果，也未将 Transformer 计划生成描述为实验完成。运行数据、截图、模型密钥、代理凭据和回退服务凭据均不提交。
