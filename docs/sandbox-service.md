# 独立沙箱服务

Compose 默认启动 API、Worker、Sandbox 三个进程。只有 Sandbox 服务挂载 Docker socket；API 和 Worker 通过内网 HTTP 使用沙箱。原来的直接 Docker SDK 模式和 Go NDJSON 服务客户端仍可用于独立部署。

`docker compose up -d --build` 构建并启动三个服务，不删除已有 research-data 数据卷。沙箱服务的命名空间使用独立 sandbox-data 卷持久化，Worker 通过 HTTP 清单对账孤立容器。

不使用 Compose 时可分别运行 `uv run openresearch serve`、`uv run openresearch worker`、`uv run openresearch sandbox`。API/Worker 设置 `OPENRESEARCH_SANDBOX_BACKEND=http`、`OPENRESEARCH_SANDBOX_URL=http://127.0.0.1:8082`。沙箱默认仅绑定本机。

## API 契约

服务使用独立的 `OPENRESEARCH_SANDBOX_TOKEN`，通过 Bearer 请求头传递，不使用模型密钥。配置为空时遵循当前本地可信部署模式。

| 路由 | 行为 |
| --- | --- |
| `GET /api/v1/health` | 检查可用执行后端 |
| `POST /api/v1/sandboxes` | 保存 plan/execution 归属并创建沙箱，返回 dk-/os- 前缀 ID |
| `GET /api/v1/sandboxes` | 当前命名空间的容器清单 |
| `GET /api/v1/sandboxes/{id}/image` | 实际 Docker 镜像摘要；后端无法提供时返回 null |
| `POST /api/v1/sandboxes/{id}/python/stream` | 接受 `{code}`，返回 NDJSON 日志和 final 执行结果 |
| `POST /api/v1/sandboxes/{id}/commands` | 接受 `{cmd: [程序, 参数...]}`，返回相同 NDJSON 契约 |
| `DELETE /api/v1/sandboxes/{id}` | 回收命名空间内的容器 |

命令参数按字面值传入容器内的 subprocess，不经过宿主机或隐式 shell。Python 文件传输也在同一个受管容器内完成。客户端不能指定宿主机挂载或替换服务配置的镜像。执行超时、流中断和清理失败会保留明确失败状态。

## OpenSandbox 回退

设置 `OPENRESEARCH_SANDBOX_FALLBACK_ENABLED=true`、`OPENRESEARCH_OPENSANDBOX_URL`（生命周期服务根地址，省略 /v1）及可选 `OPENRESEARCH_OPENSANDBOX_API_KEY`。创建 Docker 容器失败后才尝试 OpenSandbox；执行已经开始后不切换后端重跑。

也可在独立 API/Worker 部署时设置 `OPENRESEARCH_SANDBOX_BACKEND=opensandbox`，直接使用该后端；`auto` 使用 Docker 优先和可选回退。

适配器使用官方 [生命周期 API](https://github.com/opensandbox-group/OpenSandbox/blob/main/specs/sandbox-lifecycle.yml) 和 [execd API](https://github.com/opensandbox-group/OpenSandbox/blob/main/specs/execd-api.yaml)：创建、等待 Running、获取同源服务端代理 endpoint、SSE 命令执行、读取终态退出码、清单和删除。请求非 root 命令身份及默认拒绝的出站策略，资源上限从服务配置传入。不支持这些契约的旧服务会明确失败。

回退服务应由部署者配置可达地址；代码和离线协议测试不能证明任意第三方部署可用。真实外部回退验收与本机 Docker 服务验收分别记录在框架对齐清单。
