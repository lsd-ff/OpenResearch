# 可选独立意图识别服务

默认在 API 进程中执行规则和模型识别。独立服务复用同一套 IntentDraft 契约和识别规则，不维护第二份会话数据库，也不执行实验代码。

Compose 设置 `OPENRESEARCH_INTENT_SERVICE_URL=http://intent:8083`，再运行 `docker compose --profile ai up -d --build`。服务仅在 Compose 内网监听，不挂载 Docker socket 或研究数据卷。已有模型配置通过公共环境定义共享；服务之间可使用独立的 `OPENRESEARCH_INTENT_SERVICE_TOKEN`。

本机单进程启动命令为 `uv run openresearch intent`，默认监听 `127.0.0.1:8083`。`POST /v1/intent` 接受 IntentRequest（会话快照、消息和附件元数据），返回 IntentDraft。API 仅转发有界上下文和服务凭据，不转发模型密钥。

旧服务客户端可用 `POST /predict`，请求 `{"text":"复现论文并运行测试"}`，返回 `intent_type`、`confidence` 和 `entities`；使用同一真实分类器，不执行实验。旧协议没有会话字段，每次独立分类。分类器没有校准概率，`confidence=0.0` 明确表示未知，不沿用旧模拟服务的固定 `0.95`。模型未配置或调用失败仍返回 503。`GET /health` 返回 `{"status":"ok"}`，与 `/ping` 一样公开；两个分类入口均遵守独立服务令牌。

网络不可用、非成功响应或格式错误时回退进程内模型，远程调用与回退共用识别总期限。显式 Benchmark 要求在服务端再次校正，不能因为识别差异被降级。独立服务始终禁用远程识别设置，避免递归调用。
