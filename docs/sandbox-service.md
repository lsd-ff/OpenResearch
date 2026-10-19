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
| `POST /api/v1/sandboxes/{id}/python` | 接受 `{code}`，返回 JSON stdout/stderr/exit_code |
| `POST /api/v1/sandboxes/{id}/commands` | 接受 `{cmd: [程序, 参数...]}`，返回相同 JSON 执行结果 |
| `POST /api/v1/sandboxes/{id}/commands/stream` | 接受相同参数，返回 NDJSON 日志和 final 执行结果 |
| `DELETE /api/v1/sandboxes/{id}` | 回收命名空间内的容器 |

原生 Docker 后端将命令参数按字面值传入容器内的 subprocess；OpenSandbox 的字符串命令接口使用逐项 shell 引用保留参数边界。两者均不在宿主机执行实验命令。Python 文件传输也在同一个受管容器内完成。客户端不能指定宿主机挂载或替换服务配置的镜像。执行超时、流中断和清理失败会保留明确失败状态。

## OpenSandbox 回退

设置 `OPENRESEARCH_SANDBOX_FALLBACK_ENABLED=true`、`OPENRESEARCH_OPENSANDBOX_URL`（生命周期服务根地址，省略 /v1）及可选 `OPENRESEARCH_OPENSANDBOX_API_KEY`。创建 Docker 容器失败后才尝试 OpenSandbox；执行已经开始后不切换后端重跑。

也可在独立 API/Worker 部署时设置 `OPENRESEARCH_SANDBOX_BACKEND=opensandbox`，直接使用该后端；`auto` 使用 Docker 优先和可选回退。

适配器使用官方 [生命周期 API](https://github.com/opensandbox-group/OpenSandbox/blob/main/specs/sandbox-lifecycle.yml) 和 [execd API](https://github.com/opensandbox-group/OpenSandbox/blob/main/specs/execd-api.yaml)：创建、等待 Running、获取同源服务端代理 endpoint、SSE 命令执行、读取终态退出码、清单和删除。请求非 root 命令身份及默认拒绝的出站策略，资源上限从服务配置传入。不支持这些契约的旧服务会明确失败。

发布版 opensandbox-server 1.1.1 / execd v1.1.0 使用字符串命令，部分流为裸 JSON，普通输出事件会丢失行尾。适配器同时接受 SSE 和逐行 JSON，使用容器内字节帧保持原始 stdout/stderr，并查询终态命令状态取得实际退出码。非零退出缺少完成事件时仍保留实际失败；缺少成功完成证据时不声明成功。

本机已单独安装并实际验证该发布版，服务监听 127.0.0.1:18088；Sandbox 容器通过 host.docker.internal 访问。其私有配置位于忽略的 `.data/opensandbox-verification.toml`，密钥已同步至忽略的 `.env`。已有本机配置时，重启命令为：

```powershell
uv tool run --from opensandbox-server==1.1.1 opensandbox-server --config .data/opensandbox-verification.toml
```

该配置不是仓库发行文件。新环境需先部署自己的 OpenSandbox，再设置上述服务地址与凭据；Windows 使用服务端代理时还需确保其返回可达的同源地址。原生 Docker 保持默认，回退服务不在 Compose 的必需启动依赖中。真实回退、容器到服务的实际执行和清理结果见 [本轮验证记录](framework-verification.md)。
