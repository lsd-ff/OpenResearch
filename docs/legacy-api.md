# 旧执行接口迁移

Go 的 `/api/execute` 接受临时任务字段，原 Python 入口仅能选择已经保存的节点。当前在同一路径增加旧请求适配：先转换成服务端模板并持久化，再通过现有 Worker、租约、审批、预算和权限执行，需要运行实验时进入 Docker 沙箱。已接通基础操作、部分科研阶段及只读结果复核，仍不代表全部 Go 任务等价完成。8080 的既有部署尚未更新为迁移分支。

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
| `repo_discovery` | 从所属文档链接、实际 HF/GitHub 查询或明确声明发现仓库，保存候选与选择依据；下载、固定提交及执行由后续阶段核验 |
| `fix_and_rerun` | 从所属实际科研运行或其审核引用诊断结果差距，保留原记录、源码约束和验收规则，有据补丁才进入新预检及实际重跑 |
| `repo_prepare` | 固定实际仓库提交、校验文件摘要并准备受约束的适配器；返回实际清单、源码和工作区配置引用，不运行实验 |
| `ablation_design` | 在已声明的有界预算内生成、评分和选择消融候选；设计估计与实际指标分别保存 |
| `benchmark_adapter_generate` | 按所属数据及冻结规范准备真实适配器，不提前执行预检或正式实验 |
| `benchmark_adapter_preflight` | 执行必要前序阶段及最多 8 条真实样本预检，失败阻止正式阶段 |
| `benchmark_execute` | 在原工作流上通过预检后运行全部实际数据，保留预测、重算指标、环境及证据 |
| `paper_code_execute` | 走原生科研执行器，读取固定仓库/给定源码、依赖锁及冻结验收规则，实际运行并保留回执 |
| `benchmark_validate` | 引用已终止的科研阶段，从实际所属数据及保存预测重新计算声明的原生 Benchmark 指标，校验样本、摘要和回执 |
| `verify_result` | 独立复核已有科研/普通代码/数据结果，实际失败或证据不足保持验证失败，保存审核报告 |
| `paper_compare` | 读取原实验及执行前冻结的论文主张，报告实际差距与缺失证据；不生成新的论文指标 |
| `claim_evidence_build` | 对实际来源、原运行和冻结规则重新建立证据图，不把旧判定图或源码当作新执行证据 |
| `result_visualization`、`render_plot` | 将经校验的实际指标或执行次数绘制为 SVG，保存完整图表数据；不申请实验容器 |
| `framework_research` | 收集并冻结 2–4 个框架的实际公开包来源及有引用的适用性分析，返回共享目录；不安装依赖或运行实验 |
| `framework_fit_left/right`、`framework_fit_1..4` | 选择对应分支已经保存的来源与适用性结果，缺少时仅运行该分支必要的来源阶段 |
| `framework_prepare_left/right`、`framework_prepare_1..4` | 选择对应实际分支，依据冻结来源版本解析锁、冻结规则并准备真实适配器；不将锁或代码称作安装回执 |
| `framework_run_left/right`、`framework_run_1..4` | 在同图对应分支完成独立预检及真实库调用、预测和指标，保留原来源、共同数据及累计预算 |
| `framework_recommendation`、`framework_report` | 通过原生最终汇总输出来源选型或实际比较报告；失败仍明确保存，不能把分析节点完成称作实验成功 |
| `general_research` | 对实际检索元数据或所属文档生成带来源的背景分析，保存 `background_context`；空材料不能用模型记忆代替 |
| `general_synthesis` | 在同图读取既有来源及背景报告，生成 `general_response`，保留实际引用与输入摘要 |
| `general_process` | 生成只读通用回答或代码提议，未提供来源时明确未外部核验；不把回答标为代码执行或测试成功 |

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

## 独立结果复核

上述六类结果操作必须引用已有的所属工作流，不接受客户端提交的报告正文、预测、指标、代码或宿主路径。例如：

```json
{
  "task_type": "benchmark_validate",
  "task_description": "核验已有运行的实际预测与指标",
  "assigned_to": "data_agent",
  "inputs": {"workflow_ref": "openresearch:<source_plan_id>"}
}
```

源节点必须是已终止的 `research_execute`、`code_execute` 或 `data_analyze`；多个分支时用实际 `stage_task_id` 选择。`benchmark_validate`、`paper_compare` 和 `claim_evidence_build` 还要求科研来源；其余结果操作可复核普通代码或上传数据。`benchmark_validate` 也接受 `research_coding_agent`，其他五类的兼容角色为 `data_agent`，实际均路由到受限的原生 DataAgent。非所属资源返回 404，未终止来源返回 409，不支持的输入或角色返回 422。

结果复核建立新的 `Result_Review` 持久计划，执行只读 `result_audit` 节点。它有自己的服务端审批、任务超时及一次尝试预算，不修改源计划、首次起算时间、尝试次数或原实验状态，也不申请新实验容器。这与前述在同一图上继续实验阶段的行为不同。结果的 `workflow_ref` 指向新审核计划，`source_workflow_ref` 指向原运行；审核引用可以继续用于下一种结果操作，服务端展开到原来源并只保留有界的补充分析引用，不递归复制整条链。用审核的实际任务 ID 重读时不重复分析；重新提交一个未保存的新请求仍建立新的审核计划。

创建时冻结源节点的状态、执行次数、契约摘要、来源产物 ID 与 SHA。Worker 读取前再次核对这些字段、分支归属和真实文件摘要；源记录改变或混入其他生产者/执行尝试时失败并保留诊断。补充审核与原始产物分别保存，不能覆盖原执行回执。原有失败可被分析，但 `paper_compare` 或证据任务完成只表示报告已产出；`verify_result` 和 `benchmark_validate` 只有实际可核验且通过才成功，不能用分析任务的完成状态修改实验结论。

每项操作保存 `audit_report`。验证返回 `verification_report` 或 `benchmark_validation_report` / `benchmark_metrics`；论文对比另返回 `comparison_report`，对比与证据阶段均返回 `claim_evidence_graph` 和 `claim_verification_report`；绘图返回 `result_plot`、`plot_spec` 及兼容的图像字段，网页聊天显示实际审核报告。完整标签和数值保存在图表数据中，图中采用有界标签与独立指标刻度。

当前独立指标重算覆盖已声明的原生 `accuracy`、`macro_f1`、`mse`、`mae`、`r2` 协议。读取原始所属数据并核对完整样本 ID、目标及保存预测；没有 Benchmark 协议时拒绝把任意数值字段视作已验证指标。旧回执未单独保存预测原始字节，审核只能校验不可变的结构化预测，不能声称重现原始预测字节摘要；预检也只能核验已有回执、覆盖范围和独立容器，不能从未保存的预检预测重新计算指标。源码、补丁、仓库提交、实际锁、执行环境及每个已声明消融均按已有记录核对，历史缺失的环境身份明确记为限制。数值主张依据实际指标；语义主张只允许模型引用实际运行报告并经服务器校验，缺少证据保持不可验证。四行功能样例、程序断言或零退出码不证明论文完整效果。

## 框架分步比较

新框架请求可直接在描述中列出待比较名称，也可用 `inputs.frameworks` 明确声明 2–4 个名称；需要固定包、版本、导入根、数据或比较模式时，传入与原生规划相同的 `inputs.comparison`。名称推导只能使用人类请求中存在的名称，模型提议仍须通过校验。来源选型使用 `mode=research`，实际运行使用 `mode=benchmark`；Benchmark 未提供所属数据时沿用原生明确标注的共同合成功能样例，不冒充实际业务数据。

```json
{
  "task_type": "framework_research",
  "task_description": "比较 LangChain 和 LlamaIndex 的实际检索，保留共同协议和原来源",
  "assigned_to": "librarian_agent",
  "inputs": {
    "comparison": {
      "mode": "benchmark",
      "answer_mode": "none",
      "targets": [{"name": "LangChain"}, {"name": "LlamaIndex"}]
    }
  }
}
```

结果的 `workflow_ref` 持续指向同一比较图。先以该引用调用 `framework_prepare_left`、`framework_prepare_right`，再调用 `framework_run_1`、`framework_run_2`，最后请求 `framework_report`；若只做资料选型则请求 `framework_recommendation`。数字按首次声明的目标顺序，left/right 固定为 1/2；超出实际目标数、错误模式、另一分支的 `stage_task_id` 或角色不兼容返回 422。来源/fit 角色为 `librarian_agent`，prepare 为 `coder_agent`，run 接受 `sandbox_agent`、`research_coding_agent` 或 `coder_agent`，最终报告为 `data_agent`。

来源和适用性分析由同一原生来源任务共同完成，目录聚合真实保存的分支报告；后续 fit 读取该结果，不再次调用模型。直接从某个 fit 开始时只推进该分支的必要祖先，其他分支仍保留。目录记录实际包版本、元数据摘要、来源任务和报告 SHA；后续新设计固定使用这些包及版本，并核对原包约束和导入根，不能因重新查询 latest 而切换已经研究的来源。各执行分支的源码、锁、预测、失败和产物仍保持独立命名空间，只有明确共享的公开来源目录及共同协议可跨分支读取。既有原生图不新增目录或改变冻结契约，缺少所请求阶段时返回 422。

旧结果名来自实际产物投影：来源阶段返回 `framework_research_report`；fit 返回 `framework_fit_report_N` 及前两项的 `left_fit_report` / `right_fit_report`；run 返回 `framework_metrics_N` 及 `left_metrics` / `right_metrics`；最终报告返回 `evaluation_report`。prepare 的 `left_env` / `right_env` 和 `framework_environment_N` 是带所属工作流、实际阶段及锁摘要的配置声明，`state=code_and_lock_prepared`、`installation_verified=false`，不提供宿主路径或长期容器 ID。随后运行仍在新受管容器中离线安装并核验，只有实际执行回执证明安装或运行成功。原代码及入口另按科研准备阶段返回。

审批只恢复选定阶段；已终止阶段重读不重跑，切换分支或最终报告不重置首次起算时间和累计尝试。来源或某次运行失败后，只允许恢复图中既定、只读且带 `all_done` 屏障的最终框架报告；失败节点不会被重新排队。最终报告可以完成失败分析，原计划仍保持失败。预算、取消和原权限继续有效，不能通过报告入口追加修复机会或隐藏错误。

## 仓库发现

`repo_discovery` 使用完整科研图中的原生发现节点，默认只推进发现及必要的文档解析；后续通过同一 `workflow_ref` 调用 `repo_prepare`、`paper_code_execute` 或其他兼容科研阶段。可以只提供论文/方法名称、所属附件，也可声明 `inputs.search`（`kind=repository`，provider 为 auto/huggingface/github）、`paper_arxiv_id`、`paper_title`、`paper_search_query` 或 `paper_method_name`。没有显式查询时使用实际文档标题/arXiv ID，或用户已给出的名称。显式查询优先；`preferred_repo_url` / `repo_url` 是用户声明的 HTTPS GitHub 地址，`repository_ref` 保留分支或提交要求，互相冲突时拒绝。

```json
{
  "task_type": "repo_discovery",
  "task_description": "查找 Attention Is All You Need 的原始 Transformer 实现",
  "assigned_to": "coder_agent",
  "inputs": {"paper_title": "Attention Is All You Need"}
}
```

缺少给定代码时默认保留原生自动设计后继，发现阶段不会提前调用设计模型或运行实验。明确仓库、所属文档中的实际链接及真实 HF/GitHub 候选分别标记来源；地址经过相同 GitHub 格式校验。最多两条查询，匹配词、来源关联及可取得的真实星数参与有界排名；已知归档或 fork 的搜索候选排除，星数不能把没有名称对应的结果变成相关实现。无匹配、仅不相关候选或外部服务不可用分别记录，不虚构检索成功。原 Go 内置的原始 Transformer 参考回退保留为 `curated_fallback` 声明，不假装本次已查到或下载；不会拿它自动替代 Vision Transformer 等其他变体。

成功选中时顶层 `result` 和 `outputs.repo_url` 是实际规范化地址。`candidate_repositories` 保存兼容候选字段；`repository_discovery` / `repo_validation_report` 保存查询、来源、排名、选择依据及 `clone_verified=false`。该标志描述发现阶段，不在后续改写为成功。真正下载后另有含完整提交、归档及文件 SHA 的 `repo_manifest`。未选中时也保留可读发现报告和空 `repo_url`，这只表示发现任务结束；后续下载和环境阶段阻断，不执行程序。声明回退和来源排名不保证实现完整或适合论文验收。

客户端不能导入 `parsed_paper`、候选正文或执行报告作为真实来源；公共 PDF 先按原下载边界保存为所属附件，再进入真实解析器。发现产物在后续读取时重新核验实际文件摘要，不能通过续请求换仓库或代码；下载失败不继续准备执行环境。已完成发现的重读不重新查询，审批及累计预算沿用科研阶段约束。原始候选不足时可以建立明确声明首选仓库的新工作流，旧记录保留。

## 通用研究与回答

`general_research` / `general_synthesis` 在同一持久图上分步完成来源收集、背景分析和最终汇总。首请求接受所属文档字段、公共 PDF 地址、可选 `search` 及实际 `stage_task_id`，拒绝客户端声称的背景报告或执行结果。没有附件或显式查询时按用户已给出的名称做论文检索；已有附件时默认只读实际文档，除非明确提供检索。直接请求 synthesis 会先完成必要的实际来源和背景阶段。后续引用同一 `workflow_ref` 不重复检索或解析，也不能更换目标和来源。

研究角色为 `librarian_agent`，汇总为 `data_agent`；`general_process` 兼容 `general_agent`、`coder_agent`、`chat_agent`，在服务端路由到只读回答器。通用回答可以包含解释或代码提议，但没有执行权限；需要实际运行仍使用代码或科研执行入口。没有来源的回答明确为未外部核验的模型内容。研究/汇总则必须有实际可读页或检索记录，并至少引用一项，不能从空检索结果推断论文结论。

模型只选择实际检索编号，服务器绑定对应 URL、标题及原摘要片段；PDF 引文同时核对已提供片段、原始提取页、所属文档 ID 和页码。未知/重复编号、虚构 PDF 引文、错误页码及非实际来源地址拒绝。`sources` 是元数据/摘要，不表示阅读全文；`source_sha256` 是保存的结构化检索记录摘要，不是论文全文或网页原始字节摘要。文档本身另有原上传字节 SHA，缺少文本的页仍明确需要 OCR。引用存在不等于模型解释或科学结论已独立证明。

每个新回答节点保存 `<native_task_type>_validation`。语义引用检查最多两轮提议，保留原提议、拒绝原因及是否接受，沿用客户端既有有界格式重试；不删除错误引文、不替换源材料，也不重置原任务超时。两轮仍不合规保持任务失败。旧持久任务不自动增加产物权限。`background_context` / `general_response` 保存响应、原来源、原文引文及输入 SHA；顶层 `result` 展示响应、证据范围和服务器绑定的参考链接。网页使用“资料研究”或“通用回答”标签，恢复实际报告，不套用实验成功文案。

## 输入与环境生命周期

源码字段 `generated_code` / `code` 同时出现时必须相同。依赖字段 `dependency_spec` / `requirements` / `dependencies` 支持字符串列表、JSON 字符串、含 `requirements` / `dependencies` / `packages` 的对象、换行或逗号分隔字符串；版本范围内的逗号保留。多个字段须声明等价集合。公开包名称规范化，旧别名 `pytorch` → `torch`、`msgpack-python` → `msgpack`，保留版本约束。禁止 URL、额外源、pip 命令参数及当前契约不支持的 extras/marker；显式依赖最多 128 条，自动导入推导仍有独立上限。

数据和论文输入接受 `attachments`、`upload_id`、`dataset_upload_id`、`paper_upload_id`；旧 `dataset_path` / `paper_path` 仅接受 `/api/uploads/<id>/content`，必须属于当前用户。宿主文件路径、原始容器 ID 不能导入执行。

环境阶段在 `outputs` 返回 `runtime_session` / `runtime_env`，形式为 `openresearch:<plan_id>`。实际安装后才返回 `prepared_runtime` 和 `dependency_install_report`。下个请求可将同一引用放入 `inputs` 的任一等价字段，也可声明管理员注册的 `runtime_profile`；已有回执的运行时不能被静默替换。

引用保存的是不可变环境配置、依赖锁、完整解释器和镜像身份，生命周期为 `recreate_per_execution`，不是长期保活的物理沙箱。准备、安装与运行各自申请受管租约/容器；后续运行重新验证完整版本、镜像和锁，实际安装后才执行源码。探测阶段的待准备声明不会被误认为已锁定或安装。版本约束跨请求保留，后续修复不得因描述未重述固定版本而降级包；源码、断言、权限和修复预算沿用原生约束。GPU/full 资源准入与跨命名空间互斥继续生效。

输入总量、文本、ID、附件、依赖与锁闭包均有界；不兼容类型、角色、冲突字段、宿主路径和契约错误返回 Python 统一的 422，不复制 Go 入口所有错误码与临时目录语义。

## 独立结果差距修复

`fix_and_rerun` 接受所属实际科研执行或其独立审核的 `workflow_ref`，多分支使用原执行节点的 `stage_task_id`。不接受客户端粘贴的比较报告、指标、源码、依赖或容器路径。兼容角色为 `research_coding_agent` / `coder_agent`。来源必须有可完整校验的原生科研回执、固定源码和冻结规则；不支持将普通代码执行或仅有资料报告当作科研实验。原计划禁止修复、修复上限为零或资源准入被拒绝时，不能通过此入口绕过。

```json
{
  "task_type": "fix_and_rerun",
  "assigned_to": "research_coding_agent",
  "task_description": "根据已有比较诊断结果差距，保留原断言和验收规则，实际重跑并报告",
  "inputs": {"workflow_ref": "openresearch:<owned-source-plan-id>"}
}
```

服务器创建独立修复记录，`source_workflow_ref` 指向被固定的原执行，原图、结果、状态与次数不改写。新记录复制原累计尝试、预算上限及首次起算时间，独立审批并消耗新节点尝试；不能恢复完整时长或通过入口重置预算。`task_id` 断线重读不再调用模型或执行。直接重新发出未保存的新请求属于新的显式操作，不能当作同一次调用的幂等重放。

模型必须先根据原退出码、输出、指标、实际比较及原冻结规则提出补丁，即使原程序已零退出；不只重复已有的运行错误恢复。只允许修改原已授权且存在的 Python 源文件，核对当前文件摘要，保留实际使用的库、断言、raise、测试函数和测试启动器，不更换数据、指标或验收阈值。初始结果补丁和后续运行/依赖修复共享原规范不超过两次的限制；固定环境、运行时、实际库、资源准入及独立预检约束继续有效。来源在模型等待后、进入新沙箱前及正式回执返回后再次校验，改变时保留失败。

有 Benchmark 时，修改后重新用独立容器预检最多八条实际样本，预检失败阻止全量执行；随后在另一容器执行原全量数据和既定消融，重新计算实际预测指标，并按同一份冻结主张验收。格式纠正请求与语义补丁、任务尝试和实验次数分开看待。没有充分证据或无需修复时，操作可以完成诊断，状态为 `insufficient_evidence` / `not_justified`，不运行新实验，也不生成冒充重跑的 `research_report`。仅此新类型可声明可选的执行回执；其诊断产物仍强制完整，普通任务不能省略必需输出，旧契约序列化不增加空字段。

始终保存 `gap_debug_report`、`gap_patch_manifest`、`rerun_report`、`rerun_metrics`。实际运行时另保存标准科研报告、最终累计补丁、预测、冻结规则和新证据图，可继续用旧审核操作独立重算。补丁清单绑定原执行代码和实际最终代码的 SHA，原始提议和实际运行尝试分别保留；失败不覆盖原始源码。无指标的代码运行在 `rerun_metrics` 返回实际 stdout，Benchmark 返回实际指标。`rerun_completed` 只表示程序执行完成，报告同时给出冻结规则判定，差距仍存在时仍如实为 contradicted / unverifiable。网页在成功、没有有据补丁和失败时均展示实际修复报告，失败图继续保持失败。

## 兼容边界与剩余验收

单文件发行、独立 docker-core 能力和最终统一部署另按 [实施与验收](implementation-plan.md) 跟踪。本接口的基础兼容验收不替代这些范围，也不证明论文大规模训练结果。
