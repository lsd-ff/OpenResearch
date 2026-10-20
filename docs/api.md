# 当前 HTTP 契约

启动后在 /docs 查看 OpenAPI。模型拒绝未知字段，使用 snake_case，图边起点保留 from。请求最大体积受服务端限制。

## 身份

可信本地客户端使用 X-User-Id / X-Session-Id；否则生成 sa_uid / sa_sid HttpOnly Cookie。计划、上传、会话保存归属，其他身份访问得到 404。配置 API_TOKEN 时携带 Bearer 或 sa_access Cookie；浏览器在连接设置中调用 /api/auth/session 换取 Cookie，SSE 使用 withCredentials。

共享令牌与客户端声明 ID 面向可信单机使用，不提供独立账号认证。

## 接口

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | /ping、/api/health | 进程探活、沙箱就绪检查 |
| POST | /api/auth/session | 认证 Cookie |
| GET | /api/session | 当前持久会话、上传、计划、文档计划和历史 |
| POST | /api/chat | message；模型答复和校验后的页码引用 |
| POST | /api/messages | message、attachments；统一返回 chat / clarification / plan，支持持久追问 |
| POST | /api/uploads | multipart file，201；返回 id、name、sha256、size、content_url |
| GET | /api/uploads/{id}、/api/uploads/{id}/content | 元数据、原文件与 SHA ETag |
| POST | /api/sources/search | kind=paper/repository、query、limit；返回来源元数据 |
| GET | /api/pdf-proxy?url=... | 公共 HTTP/HTTPS PDF，校验 DNS、实际连接及重定向；支持 arXiv |
| POST | /api/plan | 创建图，不自动执行 |
| GET | /api/plans/{id} | plan_graph 包含状态、节点和产物索引 |
| POST | /api/plans/{id}/execute | 202，整图排队 |
| POST | /api/plans/{id}/approve、cancel | 批准或取消 |
| POST | /api/plans/{id}/tasks/{task}/execute | 202，只排队所选节点，依赖须已完成 |
| POST | /api/plans/{id}/tasks/{task}/retry | 重置失败节点及后继，需再次执行 |
| POST | /api/plans/{id}/tasks/{task}/reassign | assigned_to；仅允许尚未开始节点与兼容角色 |
| POST | /api/execute | 选择已保存节点，或以旧任务字段创建受治理的持久计划；SSE，待审批时返回 202 JSON |
| GET | /api/plans/{id}/events?after=0 | 最多 200 个事件、next_cursor |
| GET | /api/plans/{id}/stream?after=0 | plan_event SSE，支持 Last-Event-ID |
| GET | /api/plans/{id}/artifacts/{artifact}/content | 完整文本产物与 SHA ETag |
| GET | /api/plans/{id}/documents | 已解析文档、页状态及内容地址 |
| GET | /api/plans/{id}/documents/{upload}/pages/{page} | 页码、文本、needs_ocr、来源摘要 |

## 创建工作流

```json
{"intent":"计算平方和","workflow":"code","code":"print(sum(x*x for x in range(5)))"}
```

workflow 为 auto/code/paper/reproduce/benchmark。attachments 为本身份已上传文件的 ID，最多 8 个。默认 auto 且无显式 research/code 时使用统一会话规划；显式工作流仍支持高级参数。显式 paper 只解析文档。

`/api/messages` 与 `/api/plan` 的 `papers` 列表只包含本次计划实际所属的 PDF 元数据及内容地址，不包含内部文件路径或所有者字段。网页阅读按钮绑定这个列表，打开已保存的原始附件；CSV 等非 PDF 附件不显示论文入口。远程链接下载同样先保存为有归属及 SHA-256 的附件，随后才进入沙箱解析。网络、代理及大小边界见 [公共 PDF](remote-pdf.md)。

工作台统一调用 `/api/messages`，例如 `{"message":"复现 https://github.com/pallets/itsdangerous 的签名校验"}`。服务器解析意图、核验实际来源和附件，返回 type=plan 及 plan_graph；材料不足返回 type=clarification 及中文追问，不插入虚假计划。下一条简短回复沿用持久化 pending_planning。发送「取消」清除当前追问；普通聊天返回校验后的 citations。生成图后仍由 `/execute` 排队，生成计划本身不执行代码。

自动研究图增加 research_design，输出 research_spec 和 experiment_design；依赖解析成固定版本再锁定 wheel。论文分析图包含 document_extract → paper_analyze。来源检索的多个候选需要选择实际编号；模型推测的 URL 不能直接执行。消息、追问和计划插入同一事务，revision 冲突返回 409。

```json
{
  "intent": "运行指定仓库入口",
  "workflow": "reproduce",
  "research": {
    "repository": {"url": "https://github.com/owner/repo", "ref": "main"},
    "environment": {"requirements": ["blinker==1.9.0"]},
    "entrypoint": "openresearch_run.py",
    "allowed_patch_paths": ["openresearch_run.py"],
    "max_repairs": 1,
    "ablations": [],
    "rubric": {"claims": []}
  }
}
```

repository 可省略；附件中只有一个 GitHub 仓库链接时可发现来源，多个候选需要显式选择。ref 在准备时固定为完整提交 SHA。research.benchmark 包含 upload_id、format、input_columns、target_column、id_column、task 和 metrics；其协议见 experiments.md。rubric 支持来源页引用、数值阈值、容差、比较操作和指定运行 variant，规则先于实验冻结。

`/api/execute` 支持用服务端 `task_id`（可带 `plan_id`）选择已保存节点，或用 `task_type`、非空 `task_description`、`assigned_to`、`inputs` 创建旧接口兼容计划。前者仍不允许客户端类型、角色和输入覆盖持久契约；后者的外部 `task_id` 仅作来源标记，实际计划和节点 ID 由服务端生成。已接通代码、依赖/环境、所属数据/PDF 基础阶段及部分科研分步类型；未接通类型仍为 422，完整范围见 [旧接口迁移](legacy-api.md)。

科研阶段可用 `inputs.research` 创建完整冻结图，再通过结果中的 `workflow_ref` 选择后续阶段；续请求只接收该引用和可选实际 `stage_task_id`，不接受新源码等契约覆盖。只执行目标及其必要祖先，保留后继和全部已有结果；阶段完成时整图仍可能 pending。多分支同名阶段必须显式选择。审批、所属身份、累计尝试及首次执行起算时间持续生效，从其他执行入口恢复也不重置时间预算。

`benchmark_validate`、`verify_result`、`paper_compare`、`claim_evidence_build`、`result_visualization`、`render_plot` 必须以所属 `inputs.workflow_ref` 引用已终止的真实来源，可附实际 `stage_task_id` 消除分支歧义。这些操作建立独立的只读 `Result_Review` 计划，具有自身审批和一次尝试预算，不重跑实验或改变源计划。结果 `workflow_ref` 指向审核计划，`source_workflow_ref` 指向原运行；可以继续引用审核计划串联后续分析。创建与执行时均校验源契约、状态、次数、产物身份及实际文件 SHA，补充分析不能覆盖原产物。来源执行失败或证据不足时严格验证保持失败；分析任务可以完成失败报告，但不改变原实验结论。已声明原生 Benchmark 的五种指标可从所属数据和保存预测重算，历史原始字节与环境缺失仍明确记录限制。角色、实际产物及更详细边界见 [独立结果复核](legacy-api.md#独立结果复核)。

框架兼容入口支持来源研究、对应分支 fit/prepare/run、资料选型及实际比较报告，可从自然语言名称、`inputs.frameworks` 或 `inputs.comparison` 建立 2–4 目标的冻结图，再以同一 `workflow_ref` 分步推进。left/right 对应目标顺序 1/2，数字编号仅选择已有分支。来源目录冻结实际公开包版本及报告摘要，后续设计不重新选择 latest；执行分支继续隔离。prepare 仅声明代码及依赖锁已准备，不声称已安装或运行；run 仍须实际容器安装、独立预检及正式执行。失败后可生成既定只读最终报告，失败节点及原预算保持不变。字段、角色及示例见 [框架分步比较](legacy-api.md#框架分步比较)。

`repo_discovery` 在同一科研图中保存实际发现及候选，支持明确首选仓库、所属文档链接和 HF/GitHub 查询；默认只执行发现阶段，后续通过同一引用准备和运行。发现、内置参考声明、实际下载、固定提交及程序执行分别保留证据，无选中仓库时阻止后续环境与程序。`general_research` / `general_synthesis` 同图收集并汇总实际检索记录或所属文档，服务器核验来源编号与 PDF 原文引文；`general_process` 是明确未执行实验的只读回答。新任务保存有界引用纠正与拒绝回执，旧任务不扩权，审批及累计预算保持。来源范围、字段及限制见 [仓库发现](legacy-api.md#仓库发现) 和 [通用研究与回答](legacy-api.md#通用研究与回答)。

兼容入口 SSE 输出 log、result、error；流响应头 `X-OpenResearch-Plan-Id`、`X-OpenResearch-Task-Id` 返回实际 ID。开启审批时先返回 202 JSON（`approval_required`、`plan_id`、`task_id`、`plan_graph`），审批后以实际 ID 再次调用。已保存的兼容计划重读结果不会重新执行，也不会采用新提交的源码；未保存的新请求会创建新计划。首次匿名响应保留身份 Cookie。结果兼容 Go 的 `image_base_64` 并保留原 `image_base64`，`outputs` 包含本阶段实际产物。环境引用是所属配置和锁的回执，每次实际执行仍创建受管容器。该 POST 流没有游标续传；要按游标读取持久事件使用整图 GET 流。

整图 SSE 输出 plan_event，含 sequence、plan_id、task_id、execution_id、trace_id 与 payload。

整图 SSE 的浏览器连接在短暂断流后由 EventSource 携带 Last-Event-ID 自动重连；收到终态、切换会话或卸载页面才主动关闭。服务端读取终态快照之后再次检查尾部事件，避免任务在两次数据库读取之间完成时漏发结果；单节点流同样先排空晚提交的日志，再发送 result/error。重连后首次收到产物时，已经打开的空日志面板会切换到对应结果视图。日志和结果仍来自持久记录，断线不会重新执行实验。

## 状态与错误

正常计划 pending → ready → in_progress → completed；可有 awaiting_approval、failed、canceled。依赖失败的节点 blocked。单节点完成后，若仍有未执行节点，计划回到 pending。

无效状态操作 409；非法身份或游标 400；未认证 401；未知或外部身份资源 404；体积超限 413；类型不支持 415；字段、图、补丁及来源契约错误 422；模型未配置或调用失败 503。业务错误为 error，字段验证为 detail。外部来源不可用当前返回 422 并说明重试或替换来源。

日志预览有界，完整 stdout/stderr 与预测保存到不可变产物。事件必须与状态同事务提交；历史序号可以断线回放。
