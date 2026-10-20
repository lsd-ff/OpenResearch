# 旧执行接口迁移

Go 的 `/api/execute` 接受临时任务字段，原 Python 入口仅能选择已经保存的节点。当前在同一路径增加旧请求适配：先转换成服务端模板并持久化，再通过现有 Worker、租约、审批、预算、权限和 Docker 沙箱执行。当前是基础阶段兼容，不是全部 Go 任务等价完成。8080 的既有部署尚未更新为迁移分支。

## 请求与恢复

```json
{
  "task_id": "exec-1",
  "task_name": "实际运行断言",
  "task_type": "execute_code",
  "task_description": "运行原代码全部断言，不要自动修复",
  "assigned_to": "sandbox_agent",
  "inputs": {
    "generated_code": "values = [2, 3, 5, 7]\nassert sum(values) == 17\nprint(sum(values))"
  }
}
```

外部 `task_id` 可省略，仅保留在计划来源中；它不会成为文件路径或覆盖已有任务。服务端生成实际计划和节点 ID，通过流响应头 `X-OpenResearch-Plan-Id`、`X-OpenResearch-Task-Id` 返回，最终 `result` 事件也包含这两个 ID 及 `client_task_id`。只提交实际 `task_id`（可带 `plan_id`）时仍选择所属持久节点，新输入不能替换其冻结源码、角色或权限。重复提交未保存的新请求会建立新计划，不提供按外部 ID 去重。

新兼容计划按服务配置审批：需要审批时返回 202 JSON，包含 `approval_required`、实际 ID 和 `plan_graph`；批准后用实际 ID 重新 POST。已排队、执行中或终止的兼容计划重读不会重复排队，失败也不自动重跑；显式重试沿用现有节点接口。原生持久单节点仍只排队该节点，并检查依赖状态。

SSE 使用 `log`、`result`、`error`，心跳不代表任务成功。成功结果包含 `result`、`code`、`structured_data`、`image_base64`、Go 拼写 `image_base_64` 和 `outputs`；后者来自本阶段实际产物。失败保持失败事件及持久诊断。POST 流重读完整已存日志，不支持 Last-Event-ID；整图 GET `/api/plans/{id}/stream` 支持游标续传。

首次匿名调用也保留身份 Cookie，后续读取须使用相同 Cookie 或可信本地身份。其他所有者的计划、附件和配置引用返回 404。共享 API 令牌与本地身份约定仍遵循 [HTTP 契约](api.md)，没有新增独立账号认证。

## 已接通的操作

| 旧类型 | 实际行为与边界 |
| --- | --- |
| `generate_code` | 模型生成或校验所给源码，保存代码及准备报告；不安装依赖、不运行实验。附件可先解析。仅生成 GPU 代码不要求实际 GPU |
| `code_execution` | 生成或采用所给代码，走原生代码执行与有界恢复链；科研材料不足仍保持原有拒绝 |
| `execute_code` | 必须提供 `generated_code` 或 `code`，在实际容器执行，保存源码、环境、退出码和恢复记录 |
| `baseline_run`、`run_benchmark` | Go 沙箱执行别名，同 `execute_code`；不等价于完整 Research Coding Benchmark 协议 |
| `resolve_dependencies` | 显式声明或按源码静态导入推导公开包，使用实际解释器及 uv 锁定、校验 wheel；不安装 |
| `prepare_runtime` | 只探测实际解释器/镜像及保存待准备声明，不解析下载或安装包；不把声明当安装成功 |
| `install_dependencies` | 必须引用所属准备回执，实际解析/复核锁并在新受管容器离线安装，保存真实安装回执 |
| `dataset_profile` | 分析所属上传数据，返回实际统计及图像，使用原生 DataAgent |
| `paper_parse` | 解析所属 PDF 上传；也可通过 `pdf_url` / `paper_url` 下载公共 PDF 后解析，沿用固定公共地址连接与重定向校验 |
| `claim_rubric_extract` | 读取实际 PDF 页后提取并冻结主张，核对引文、页码与来源 SHA；只推进到主张阶段 |
| `repo_prepare` | 固定实际仓库提交、校验文件摘要并准备受约束的适配器；返回实际清单、源码和工作区配置引用，不运行实验 |
| `ablation_design` | 在已声明的有界预算内生成、评分和选择消融候选；设计估计与实际指标分别保存 |
| `benchmark_adapter_generate` | 按所属数据及冻结规范准备真实适配器，不提前执行预检或正式实验 |
| `benchmark_adapter_preflight` | 执行必要前序阶段及最多 8 条真实样本预检，失败阻止正式阶段 |
| `benchmark_execute` | 在原工作流上通过预检后运行全部实际数据，保留预测、重算指标、环境及证据 |
| `paper_code_execute` | 走原生科研执行器，读取固定仓库/给定源码、依赖锁及冻结验收规则，实际运行并保留回执 |

拼写别名：`code_generate` → `generate_code`；`code_execute` → `execute_code`；`data_analyze` → `dataset_profile`；`document_extract` → `paper_parse`；`env_setup` / `prepare_environment` / `setup_runtime` / `test_environment` → `prepare_runtime`；`dependency_install` → `install_dependencies`。

省略类型时，`sandbox_agent` 默认执行代码，`data_agent` 默认分析数据，`librarian_agent` 默认解析 PDF，其他默认生成代码。显式角色必须与操作兼容，不接受客户端执行器权限。

## 科研阶段续执行

这些科研类型保留完整持久图，只将目标及其必要祖先纳入本次执行范围，不删除后继，不将阶段成功冒充整图完成。首次请求可在 `inputs.research` 声明与 `/api/plan` 相同的 `ResearchSpec`，提供给定 `code` / `generated_code`、所属论文附件或实际仓库；`inputs.auto_design=true` 可走原生自动设计。Benchmark 必须声明所属数据、输入/目标/ID 列及指标；目标列仍由评估端保存，不交给预测程序。`repo_url`、精确版本 `dependency_spec` 和管理员注册 `runtime_profile` 可作为便捷声明，与 `research` 冲突时拒绝，不静默丢弃约束；科研固定环境当前只接受精确版本，范围声明仍使用原生自动设计或独立依赖入口。

阶段结果包含 `workflow_ref: openresearch:<plan_id>`。后续请求只选择同一份受管工作流，例如：

```json
{
  "task_type": "benchmark_adapter_preflight",
  "task_description": "继续既定工作流，完成真实样本预检",
  "assigned_to": "research_coding_agent",
  "inputs": {"workflow_ref": "openresearch:<plan_id>"}
}
```

通过后，将类型改为 `benchmark_execute` 即可运行正式阶段；已经完成的前序节点、源码、数据和锁不会重新生成。引用也可选择由原生 `/api/plan` 创建的所属科研计划。多框架图存在多个同类型分支时必须额外指定实际 `stage_task_id`，服务端核对其类型与分支，不任意挑选。此选择能力不代表旧框架研究/准备名称已经全部适配。

续请求的 `inputs` 仅接受 `workflow_ref` 与可选 `stage_task_id`；新源码、数据、运行时和客户端声称的执行报告不能替换冻结契约。相同阶段正在执行时可重读；另一个尚不在当前范围内的阶段返回 409。终态重读不重跑，失败必须显式重试；审批后用服务端实际任务 ID 恢复时也只推进原选中阶段。累计节点尝试和首次执行起算时间贯穿阶段，切换到整图/原生单节点入口也不重置时间预算。

`repo_prepare` / `benchmark_adapter_generate` 的 `code` 和 `outputs.generated_code` 来自实际快照与校验补丁合成后的入口；同时返回相对 `code_file_path`、`workspace_ref`，有仓库时返回真实 `repo_manifest`。旧 `workspace_path` 字段也是同一配置引用，不是宿主目录。主张阶段另返回基于实际冻结产物的 `claim_rubric_report`。SSE 按所选节点终态结束，此时整图可能仍为 pending，未请求的后继不会执行。

候选消融参数只包含应用参数；Python 与入口脚本由执行器添加。重复脚本/解释器被拒，最多两次模型候选请求，原提议和错误写入 `proposal_rejections`；失败可使用明确标注的已有源码/设计候选，但回退也须通过相同参数校验。两次模型提议及一次回退的拒绝记录最多三组，没有可用候选则保留失败审计并阻止执行。纠正不静默删改 argv，不将设计估计标为实测。

## 输入与环境生命周期

源码字段 `generated_code` / `code` 同时出现时必须相同。依赖字段 `dependency_spec` / `requirements` / `dependencies` 支持字符串列表、JSON 字符串、含 `requirements` / `dependencies` / `packages` 的对象、换行或逗号分隔字符串；版本范围内的逗号保留。多个字段须声明等价集合。公开包名称规范化，旧别名 `pytorch` → `torch`、`msgpack-python` → `msgpack`，保留版本约束。禁止 URL、额外源、pip 命令参数及当前契约不支持的 extras/marker；显式依赖最多 128 条，自动导入推导仍有独立上限。

数据和论文输入接受 `attachments`、`upload_id`、`dataset_upload_id`、`paper_upload_id`；旧 `dataset_path` / `paper_path` 仅接受 `/api/uploads/<id>/content`，必须属于当前用户。宿主文件路径、原始容器 ID 不能导入执行。

环境阶段在 `outputs` 返回 `runtime_session` / `runtime_env`，形式为 `openresearch:<plan_id>`。实际安装后才返回 `prepared_runtime` 和 `dependency_install_report`。下个请求可将同一引用放入 `inputs` 的任一等价字段，也可声明管理员注册的 `runtime_profile`；已有回执的运行时不能被静默替换。

引用保存的是不可变环境配置、依赖锁、完整解释器和镜像身份，生命周期为 `recreate_per_execution`，不是长期保活的物理沙箱。准备、安装与运行各自申请受管租约/容器；后续运行重新验证完整版本、镜像和锁，实际安装后才执行源码。探测阶段的待准备声明不会被误认为已锁定或安装。版本约束跨请求保留，后续修复不得因描述未重述固定版本而降级包；源码、断言、权限和修复预算沿用原生约束。GPU/full 资源准入与跨命名空间互斥继续生效。

输入总量、文本、ID、附件、依赖与锁闭包均有界；不兼容类型、角色、冲突字段、宿主路径和契约错误返回 Python 统一的 422，不复制 Go 入口所有错误码与临时目录语义。

## 尚未完成的旧阶段

以下类型需要将前序来源、冻结验收规则及实际产物接入受治理的持续计划，当前旧入口返回 422；部分能力已存在于 Python 原生完整工作流，不能因此声称这些旧分步请求已经兼容。

| 范围 | 尚未适配的 Go 类型 |
| --- | --- |
| 框架研究与对比 | `framework_research`、`framework_recommendation`，以及按分支生成的 fit/index/prepare 类型 |
| 论文、仓库与证据 | `repo_discovery`、`paper_compare`、`claim_evidence_build` |
| 结果及恢复阶段 | `result_visualization`、`fix_and_rerun`、`verify_result`、`render_plot` |
| 通用研究 | `general_research`、`general_synthesis`、`general_process` |
| 独立 Benchmark 阶段 | `benchmark_validate`（原生执行器已核验指标，旧独立阶段尚未适配） |

单文件发行、独立 docker-core 能力和最终统一部署另按 [实施与验收](implementation-plan.md) 跟踪。本接口的基础兼容验收不替代这些范围，也不证明论文大规模训练结果。
