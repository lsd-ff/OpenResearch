# 验证记录

日期：2026-10-10。开发遵循项目 code_skill。初次基础验收使用 MockTransport；用户提供配置后，追加 AutoDL / DeepSeek-V4-Flash 的真实模型验收。密钥保存在忽略的本地 .env，报告不记录密钥。外部来源验收实际访问 GitHub/PyPI。

## 已实际运行

| 检查 | 结果 |
| --- | --- |
| Windows / Python 3.13.5：Ruff、格式、mypy strict | 通过，111 个源文件 |
| Windows 离线行为测试 | 132 通过，15 Docker 用例不在默认门禁运行 |
| Windows 驱动 Docker Desktop Linux Engine：真实沙箱验收 | 15 通过，含真实 GitHub/PyPI 和容器实际 Python 条件依赖检查 |
| Linux 容器 / Python 3.12.13：离线行为测试 | 132 通过 |
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

配置保持用户提供的 Anthropic BASE_URL 和模型别名；预检返回 HTTP 200，供应商响应标记为 DeepSeek-V4-Flash-0731。该字段只记录供应商声明，未独立确认模型权重。运行采用 Anthropic submit_response 的 input_schema、关闭扩展思考、4096 输出 token、180 秒模型总期限、480 秒任务和 1800 秒计划预算。

`scripts/verify_model.py` 完整运行通过：2026-10-10 13:08:00–13:09:59（北京时间）。本地记录：`.data/model-acceptance/model-acceptance-d786a9f81e0242b38db0050e0fd13e61/result.json`。文档、代码、研究和修复计划分别为 27ea1c2932aa460888687cee156f82b9、6b199d71615e48e39fd15b6c2f6cd9b4、40e22c93760a4751a2664372cde6b686、34dfc7a2f82640ec98d4ebf2469396d2。

- 合成 PDF 真实解析；模型翻译声明并引用实际上传 ID、第一页和原文；服务端引用校验通过。
- 模型生成的计算程序在 Docker 中输出 MODEL_GENERATION_OK 和 55。
- 公共仓库固定提交，模型抽取两个带来源的冻结主张，生成补丁；真实基线 accuracy=1.0、消融=0.5，两个协议主张通过验收。
- 故意错误的入口第一次失败；真实模型提出修复，第二次在新沙箱运行成功，基线与消融分数仍为 1.0 / 0.5。
- 语义模型只获得最终执行输出与重算指标；源码补丁和早期失败输出不能作为成功证据，原始报告引用同时核对。

联调曾发现证据字段类型不符、原任务时限不足和偶发模型请求失败；现已提供完整 Schema、有界格式修正、结构化响应工具和分层预算。首次浏览器 503 的供应商响应未保留，无法回溯其精确原因。后续在同一会话复现格式失败，脱敏协议诊断确认供应商返回 end_turn/text，忽略所请求的响应工具，且正式文本不是 JSON。现将完整正式文本的 JSON 格式错误纳入同一次有界修正，总期限不变；不从正文猜测 JSON，不重试未知工具、截断或 HTTP 错误。

修正后同一会话的真实模型请求与浏览器聊天均成功；2026-10-10 13:21 网页显示“模型已连接，可以开始使用。”，截图为 `.data/browser-acceptance/model-connected-20261010-1322.png`，此前失败记录保留。API/Worker 均核对为用户指定的 Anthropic 地址与模型。最终镜像追加 HTTP 部署复核：计划 68b33b0afed34b84bcd6a6c257b3f11a 完成，基线/消融仍为 1.0 / 0.5；单节点计划 dbe97d45df8d42eaaf16bbc53da0d2fd 完成。密钥检查确认私有 .env 被 Git 忽略，前端和发布包共 409 个文件中未发现密钥。模型错误按脱敏类别记录，界面区分鉴权、限流、超时和格式问题，不把失败显示为成功。

Docker 修复测试的传输替身改为按 user 角色读取上下文，适应新增 Schema 消息；仍验证三次真实容器与累计补丁。Linux 并发负载下的取消测试启动等待改为 15 秒，取消后的清理期限仍独立保持 5 秒；完整重跑通过。

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

原 Go HTTP 沙箱适配器验证了流错误处理，没有启动原服务进行完整端到端验收；原协议不能提供镜像摘要与 label 清单。远程 GitHub Actions 已配置，尚无远程运行记录。Python 3.14.5 仅验证 wheel 安装和迁移，没有运行完整行为测试集。

Starlette TestClient 提示旧 HTTPX 接口弃用；Linux 只读源码挂载产生 pytest cache 无法写入提示，测试本身通过。保留的 PDF.js 依赖构建含 eval 提示，阅读参数已设置 isEvalSupported=false；Vite 同时提示大 chunk，未进行全依赖安全审计或首屏体积优化。
