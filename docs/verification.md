# 验证记录

最新的论文实际执行见 [Transformer 核心复现实测](transformer-reproduction.md)：完整图 7/7、程序退出码 0、257 项工程测试、22 项真实执行/外部验收、8 项网页回归。此前 [架构补齐验证](framework-verification.md) 和下文保留原阶段计数与回执，供追溯。

日期：2026-10-10。开发遵循项目 code_skill。初次基础验收使用 MockTransport；用户提供配置后，追加 AutoDL / DeepSeek-V4-Flash 的真实模型验收。密钥保存在忽略的本地 .env，报告不记录密钥。外部来源验收实际访问 GitHub/PyPI。

## 已实际运行

| 检查 | 结果 |
| --- | --- |
| Windows / Python 3.13.5：Ruff、格式、mypy strict | 2026-10-19 复核通过，125 个源文件 |
| Windows 离线行为测试 | 2026-10-19 复核 178 通过，15 Docker 用例不在默认门禁运行 |
| Windows 驱动 Docker Desktop Linux Engine：真实沙箱验收 | 15 通过，含真实 GitHub/PyPI 和容器实际 Python 条件依赖检查 |
| GitHub Actions Windows / Ubuntu，Python 3.12 / 3.13 | 2026-10-19 提交 1512559：各矩阵 178 通过；Ruff、mypy、打包、前端门禁全部通过 |
| GitHub Actions Docker 与 Chromium 网页 | Docker 14 通过、1 个联网用例未启用；网页 4 通过，含名称检索、真实 Docker 输出和刷新恢复 |
| React ESLint、TypeScript、Vite | 通过 |
| 多阶段 Docker 镜像构建、Compose API/Worker | 构建和启动通过 |
| Linux HTTP Benchmark | 基线 accuracy=1.0，消融=0.5；下载报告、预测与 SSE；镜像摘要存在 |
| Linux HTTP 单节点与角色重分配 | 准备节点单独完成，不运行后继；再执行后继，完整计划完成 |
| Linux API 重启 | 计划、事件、会话仍可读取 |
| 浏览器 | 配置→上传 CSV→列映射→冻结阈值→执行→证据图；刷新恢复状态；上传 PDF 原文可读 |
| 模型缺失的翻译路径 | 浏览器明确报错，不显示演示成功 |
| 真实 AutoDL 模型链 | 对话、PDF 引用与短文翻译、代码生成、主张抽取、补丁、数值/语义评估、真实修复通过 |
| uv build | sdist 与 wheel 构建通过；包含迁移与沙箱可信程序 |
| wheel 独立安装 / Python 3.14.5 | 安装成功，0001 → 0002 迁移成功；包中无运行数据或私有 .env |

离线测试覆盖 DAG、来源契约、补丁基线和范围、全量预测校验、指标重算、冻结规则摘要、引用核对、会话 revision、归属隔离、并发领取、租约恢复、迟到结果、预算、取消、SSE 游标、认证、产物哈希与 GC。

真实 Docker 用例解析含文字和空白页 PDF，拒绝损坏及超页文档；运行正常、异常、超时、取消和超输出程序并检查清理。容器对账保留其他 namespace；失败预测保留证据。修复用例使用受控模型传输层提出补丁，但三个尝试均在新容器真实运行。

真实仓库用例固定 pallets/itsdangerous 的提交，执行其中源码入口；真实依赖用例下载 blinker 1.9.0 wheel，按 SHA 锁在无网络容器中安装并验证版本。小型 Benchmark 只是四行协议验收数据，不构成论文科学效果证据。

浏览器验收发现并修复了刷新后 React Flow 尺寸丢失导致节点不可见的问题，并复核了完成状态与证据恢复。截图保存在本地运行目录 .data/browser-acceptance/benchmark-evidence.jpg，不提交运行数据。

## 真实模型追加验收

2026-10-19：工作台和 Worker 已恢复启动，`scripts/check.py`、前端 ESLint / TypeScript / Vite 已重新通过。聊天自动规划的真实模型验收通过以下三条独立计划，全程不提交手动 ResearchSpec：

- Benchmark `89fc55734c7f480494e39e2a7acbfa6f`：上传 CSV 后追问歧义标签，短答无需重传；自动生成入口、锁定依赖，真实源码基线 accuracy=1.0、消融=0.5。实际导入 `/repository/src/itsdangerous/__init__.py`，报告记录沙箱镜像摘要。
- PDF 分析 `d69968475f0d40e98dcbf9d2a1513edd`：在 Docker 解析上传原文，两个引用逐字匹配第一页文本。
- PDF 复现 `ac8c126b3a924846808036590c930c74`：消息不包含仓库地址，由原文发现 pallets/itsdangerous；七个节点完成自动设计、依赖锁定、规则冻结、补丁和真实源码执行，输出 AUTO_ROUNDTRIP_CONFIRMED 与实际源码路径。

原始验收 `auto-acceptance-14f89b113c7e445ca29588b7ae9fdeef` 在前两条成功后，第三条规划收到 422，失败记录保留。离线复现确认模型将显式论文复现误分为 code 会进入旧拒绝路径，已增加来源要求保护和两个行为用例；原供应商分类响应未保留，不能据此断言最初 422 的精确内部原因。再次通过相同聊天与附件建立的第三条计划已完成；恢复记录重新读取并核验前两条的原始产物，未重算或改写失败记录。本地回执：`.data/auto-acceptance/recovery-4eb5e5bc04604f9c8efde9cb50aa7eed/result.json`，复现执行时间 2026-10-19 08:58:19–09:00:31（北京时间）。这些协议夹具不构成科学论文效果证据。

网页 CI 首次启动暴露 API/Worker 并发打开新库时 WAL 切换立即返回 SQLITE_BUSY 的问题。已对该阶段的锁错误增加有界重试，其它数据库错误继续抛出；回归用例持有真实 SQLite 写锁，验证等待、解锁后迁移和原数据保留。将旧启动逻辑注入同一用例可稳定复现失败。Playwright 的 API 使用独立目录、端口和本地分类夹具，避免读取实际模型凭据和工作数据。

后续网页 trace 确认悬浮的高级设置按钮遮住了运行按钮，已改为占据布局空间的设置工具栏，并按剩余空间计算移动端高度。最终 [CI 39184283140](https://github.com/lsd-ff/OpenResearch-Python/actions/runs/39184283140) 的三个网页用例全部通过：上传 CSV 后追问、刷新后短答生成六节点计划（只上传一次、无需高级表单）；正常点击运行 smoke，经真实 Docker 得到 sum=15，刷新后恢复两节点完成状态及输出；390px 窄屏设置与运行按钮可点击，高级表单可正常打开。未使用强制点击或直接调用 API 代替网页操作。分类夹具只返回 IntentDraft，其它模型契约会被拒绝；真实模型自动设计与实验结果由上面的三条计划独立证明。

最终部署复核的 Benchmark 为 `b5ea4cea5caf4a199d3dc3ec766654df`，基线/消融仍为 1.0 / 0.5；单节点与重分配计划 `babf194fe9c5470091d96de498a1c3b9` 完成。更新工具栏并重启后，三个模型计划仍可读取为 completed；API/Worker 的 124 个 Python 源文件摘要和模型配置与本地一致，工作台实际提供新工具栏样式。317 个提交/构建文件未发现本机模型密钥，审计回执位于 `.data/final-audit.json`。运行数据、凭据和浏览器截图均不提交；网页截图与 trace 由 CI 产物保留 7 天。

配置保持用户提供的 Anthropic BASE_URL 和模型别名；预检返回 HTTP 200，供应商响应标记为 DeepSeek-V4-Flash-0731。该字段只记录供应商声明，未独立确认模型权重。当前运行采用 Anthropic submit_response 的 input_schema、关闭扩展思考、8192 输出 token、300 秒模型总期限、480 秒任务和 1800 秒计划预算。

`scripts/verify_model.py` 完整运行通过：2026-10-10 13:08:00–13:09:59（北京时间）。本地记录：`.data/model-acceptance/model-acceptance-d786a9f81e0242b38db0050e0fd13e61/result.json`。文档、代码、研究和修复计划分别为 27ea1c2932aa460888687cee156f82b9、6b199d71615e48e39fd15b6c2f6cd9b4、40e22c93760a4751a2664372cde6b686、34dfc7a2f82640ec98d4ebf2469396d2。

- 合成 PDF 真实解析；模型翻译声明并引用实际上传 ID、第一页和原文；服务端引用校验通过。
- 模型生成的计算程序在 Docker 中输出 MODEL_GENERATION_OK 和 55。
- 公共仓库固定提交，模型抽取两个带来源的冻结主张，生成补丁；真实基线 accuracy=1.0、消融=0.5，两个协议主张通过验收。
- 故意错误的入口第一次失败；真实模型提出修复，第二次在新沙箱运行成功，基线与消融分数仍为 1.0 / 0.5。
- 语义模型只获得最终执行输出与重算指标；源码补丁和早期失败输出不能作为成功证据，原始报告引用同时核对。

联调曾发现证据字段类型不符、原任务时限不足和偶发模型请求失败；现已提供完整 Schema、有界格式修正、结构化响应工具和分层预算。首次浏览器 503 的供应商响应未保留，无法回溯其精确原因。后续在同一会话复现格式失败，脱敏协议诊断确认供应商返回 end_turn/text，忽略所请求的响应工具，且正式文本不是 JSON。现将完整正式文本的 JSON 格式错误纳入同一次有界修正，总期限不变；不从正文猜测 JSON，不重试未知工具、截断或 HTTP 错误。

修正后同一会话的真实模型请求与浏览器聊天均成功；2026-10-10 13:21 网页显示“模型已连接，可以开始使用。”，截图为 `.data/browser-acceptance/model-connected-20261010-1322.png`，此前失败记录保留。API/Worker 均核对为用户指定的 Anthropic 地址与模型。最终镜像追加 HTTP 部署复核：计划 68b33b0afed34b84bcd6a6c257b3f11a 完成，基线/消融仍为 1.0 / 0.5；单节点计划 dbe97d45df8d42eaaf16bbc53da0d2fd 完成。密钥检查确认私有 .env 被 Git 忽略，前端和发布包共 409 个文件中未发现密钥。模型错误按脱敏类别记录，界面区分鉴权、限流、超时和格式问题，不把失败显示为成功。

Docker 修复测试的传输替身改为按 user 角色读取上下文，适应新增 Schema 消息；仍验证三次真实容器与累计补丁。Linux 并发负载下的取消测试启动等待改为 15 秒，取消后的清理期限仍独立保持 5 秒；完整重跑通过。

## 仅给论文名称的追加回归

用户提供的原话是「复现一下 Transformer 论文的核心架构并跑通测试」，没有链接或附件。此前没有覆盖完整的命名检索路径，模型漏填检索词、查询无结果或附带无效仓库地址时，可能提前要求用户补来源。现在使用原话中的名称做最多一次查询回退；模型猜测的地址不能中断发现过程，用户提供的实际链接优先，来源仍必须来自真实检索候选。真正未找到来源或服务不可用时继续说明原因，不生成假仓库。

现场探测得到的 `Transformer: Attention Is All You Need` 查询和 `Transformer` 查询都返回了实际 GitHub 候选，因此不能断定原截图那次就是查询无结果。真实接口首次追加验收确实收到“请提供 HTTPS GitHub 仓库地址”，失败记录 `named-source-fc5c614140ab4c04a6b9dc3dc7c854d6` 保留；定位并修复了模型无效 URL 提前中止的分支，没有删除这次失败记录。

2026-10-19 09:34:06–09:34:27（北京时间）通过真实配置模型与 GitHub API 再次发送同一句话，自动选中 [jadore801120/attention-is-all-you-need-pytorch](https://github.com/jadore801120/attention-is-all-you-need-pytorch)，生成六节点计划 `4685a042c2314f28ab03e09080567e03`，并通过 GitHub 仓库元数据核对地址。没有手填来源或上传文件，也没有残留材料追问。本地回执 `.data/named-source-acceptance/named-source-bfb4e6de447d4b1dbe6b5b4f59025dd0/result.json` 标记为 `planning_only`：本次验证自动检索与计划生成，未执行 Transformer 程序或证明其论文效果。

旧浏览器会话只替换首条已知产品欢迎语，其余用户消息、历史回答和计划保持原值，历史追问不会被伪造为新成功结果。[CI 39185784577](https://github.com/lsd-ff/OpenResearch-Python/actions/runs/39185784577) 全部通过：178 项离线测试覆盖空查询、无结果、查询失败、模型无效地址、用户真实链接优先及不支持的显式 HTTP 地址；新增网页用例从旧会话出发点击同一条 Transformer 推荐指令，最终产生计划，刷新后仍保留原记录。网页分类、候选选择和搜索为受控夹具，不伪造执行结果；真实联网的计划生成由上一段单独证明。生产 API/Worker 的 125 个 Python 源文件摘要均与本地一致。

## 重跑

```powershell
uv run --locked python scripts/check.py
$env:OPENRESEARCH_TEST_DOCKER = '1'
$env:OPENRESEARCH_TEST_EXTERNAL = '1'
uv run --locked pytest -m docker
```

前端运行 npm ci、npm run lint、npm run build。启动 API/Worker 或 Compose 后运行 `uv run --locked python scripts/verify_deployment.py`，可用 --url 调整端口。

真实模型验收显式运行 `uv run --locked python scripts/verify_model.py`，会调用收费接口；默认测试不会调用真实供应商。配置说明见 [models.md](models.md)。

## 验收边界与已知提示

GitHub 仓库检索实际返回 200。本次 arXiv API 返回 429 Rate exceeded，应用明确报告来源不可用；arXiv 元数据解析和 URL 限制有行为测试，但没有获得本次实时检索成功证据。

OpenAI-compatible 与 Anthropic 的请求、响应工具、格式修正、截止时间、代码生成、修复及语义证据契约有传输层测试；AutoDL 小型真实模型链已通过。未验证模型科研判断的整体质量、长文翻译或真实论文复现效果。全文翻译限定 100 页 / 12000 字符，超限需划词处理。扫描 PDF 无 OCR。

原 Go HTTP 沙箱适配器验证了流错误处理，没有启动原服务进行完整端到端验收；原协议不能提供镜像摘要与 label 清单。远程 GitHub Actions 的 Windows/Ubuntu 与 Python 3.12/3.13 矩阵、Docker、前端和网页门禁已运行成功，见 [运行记录](https://github.com/lsd-ff/OpenResearch-Python/actions/runs/39185784577)。Python 3.14.5 仅验证 wheel 安装和迁移，没有运行完整行为测试集。

Starlette TestClient 提示旧 HTTPX 接口弃用；Linux 只读源码挂载产生 pytest cache 无法写入提示，测试本身通过。保留的 PDF.js 依赖构建含 eval 提示，阅读参数已设置 isEvalSupported=false；Vite 同时提示大 chunk，未进行全依赖安全审计或首屏体积优化。
