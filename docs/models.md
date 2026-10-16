# 模型配置与联调

API 和 Worker 共用模型配置。模型密钥只保存在本机被 Git 忽略的 `.env`，不写入前端、浏览器存储、示例配置或验收报告。

## 支持的协议

`OPENRESEARCH_LLM_PROVIDER=openai` 使用 BASE_URL 后的 `/chat/completions`，Bearer 鉴权与 JSON response_format。`anthropic` 使用 `/v1/messages`，x-api-key 与 anthropic-version；若 BASE_URL 已以 `/v1` 结尾，只追加 `/messages`。Anthropic 的 system 提示单独放在顶层；读取正式 text 或明确请求的结构化响应工具，不读取 reasoning，不执行工具操作。

AutoDL 配置示例（密钥需自行替换）：

```dotenv
OPENRESEARCH_LLM_PROVIDER=anthropic
OPENRESEARCH_LLM_BASE_URL=https://www.autodl.art/api/v1/anthropic
OPENRESEARCH_LLM_MODEL=DeepSeek-V4-Flash
OPENRESEARCH_LLM_API_KEY=YOUR_API_KEY
OPENRESEARCH_LLM_TIMEOUT_SECONDS=180
OPENRESEARCH_LLM_MAX_OUTPUT_TOKENS=4096
OPENRESEARCH_LLM_SCHEMA_RETRIES=1
OPENRESEARCH_LLM_THINKING=disabled
OPENRESEARCH_LLM_STRUCTURED_OUTPUT=tool
OPENRESEARCH_TASK_TIMEOUT_SECONDS=480
OPENRESEARCH_PLAN_TIMEOUT_SECONDS=1800
```

该地址对应的协议见 [AutoDL 官方示例](https://autodl.art/docs/claude-sonnet-4-6-cc/)，Messages 参数见 [Anthropic API 文档](https://platform.claude.com/docs/en/api/messages/create)。模型名称以所用供应商的可用模型为准，不由客户端自动替换。

上述样例关闭扩展思考，并为模型请求、含修复的任务与整体计划分别设置预算。`LLM_THINKING=default` 不发送 thinking 字段；`disabled` 仅用于支持此参数的供应商。[DeepSeek 官方参数说明](https://api-docs.deepseek.com/api/create-chat-completion/)介绍了该开关。本次 AutoDL 接口实际接受 disabled；不据此假定所有 OpenAI-compatible 接口支持它。

修改 `.env` 后，在 Python 工程目录执行 `docker compose up -d --wait`，重建 API 和 Worker 容器以更新环境变量；单纯 `docker compose restart` 不会更新容器配置。修改程序代码后使用 `docker compose up -d --build --wait`。本机 uv 运行时重启 serve 和 worker。

## 结构化输出与失败处理

所有能力向模型提供完整 Pydantic JSON Schema，并继续验证上传页码引用、补丁路径与原始哈希、冻结规则和证据原文。完整正式响应的 JSON 格式、类型或必填字段错误默认允许一次格式修正，次数可设置为 0–2；修正仍须满足同一契约，HTTP 错误不以此机制重试。Anthropic 通过提示要求单个 JSON 对象；如果供应商返回完整 JSON 代码围栏，可以整体解包，但不会从解释性文本中猜测或截取 JSON。供应商忽略结构化工具而返回普通文本时，要求其重新提交结构化结果，不把该文本直接包装成成功回答。

Anthropic 可选 `LLM_STRUCTURED_OUTPUT=tool`：通过唯一的 submit_response 工具 input_schema 请求数据。工具响应只接收该工具的单个 JSON 对象，正式 text 的完整 JSON 对象也须经过同一契约；不执行任何工具调用，未知工具、重复调用和截断响应均拒绝。默认 prompt 模式保留 JSON 提示与校验，OpenAI 协议继续使用 JSON response_format。工具选择需供应商和模型支持，见 [Anthropic 工具定义说明](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)。本次 AutoDL 配置实际支持此方式。

输出达到 token 上限、模型拒绝、未知工具调用、输入或输出超限都会明确失败。HTTP 状态码可用于区分鉴权或限流错误；错误不会携带供应商正文、密钥或 reasoning。模型总期限包含格式修正、连接等待与慢速响应，任务本身仍受独立的执行预算约束。浏览器聊天等待覆盖模型总期限上限。

模型只提出代码或评估建议。生成代码仍在无网络 Docker 沙箱运行；数值指标由服务端重算，语义评估只获得最终运行尝试和重算指标，排除补丁源码和早期失败尝试，并同时核对原始报告引用。有效引用只说明证据存在，不保证模型的推理正确。

## 真实模型验收

默认测试不调用真实供应商。配置模型并启动 API/Worker 后，显式运行：

```powershell
uv run --locked python scripts/verify_model.py
```

此命令会产生模型调用费用，上传合成 PDF/四行 CSV，创建独立验收会话，并验证真实对话、PDF 解析和引用、短文翻译、模型生成代码、论文式主张抽取、受限补丁、基线与消融、语义证据，以及故意失败后的模型修复。公共仓库样本使用 pallets/itsdangerous，其分支在获取时固定为提交 SHA。

生成和修复的程序都由部署的 Worker 在 Docker 中运行。脚本只读取产物；JSON 验收结果和报告保存在 `.data/model-acceptance/<运行 ID>/`，不会覆盖旧验收。它验证的是小型软件协议链，不证明真实论文复现、长文翻译或模型科研判断的整体质量。
