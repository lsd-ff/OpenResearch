# 命名运行时与目标 Python 验证

2026-10-20：本批交付受配置约束的沙箱运行时基础。自动科研计划选择、版本不匹配后的自动切换、GPU/full 模式与资源准入尚未接完，不能把本页验收当作完整训练调度已经完成。

## 配置契约

`OPENRESEARCH_RUNTIME_PROFILES` 默认为空对象，保留已有默认镜像和旧计划行为。管理员可在 API、Worker、Sandbox 使用相同的 JSON 注册命名配置，例如：

```json
{
  "cpu11": {"image": "python:3.11-slim-bookworm", "python": "3.11", "memory_mb": 256},
  "cpu13": {"image": "python:3.13-slim-bookworm", "python": "3.13", "memory_mb": 256},
  "cuda12": {"image": "python:3.12-slim-bookworm", "python": "3.12", "gpu": true, "memory_mb": 512}
}
```

最多 16 项，支持目标 CPython 3.11、3.12、3.13。每项限制镜像引用、CPU、内存及是否请求 GPU。仅管理员配置可以指定镜像，调用方只能引用已有名称。当前 GPU 请求透传可见设备，不提供显存配额或跨 Worker 的 GPU 并发准入；不要将其作为多任务 GPU 调度能力。

原生 Docker 保留非 root、只读根目录、capability 清除、默认无网络及工作区/进程限制。命名 GPU 配置使用实际 Docker DeviceRequest。HTTP 服务确认名称与配置 SHA-256；服务端注册不同、旧服务忽略名称时，客户端清理已创建容器并失败，不能静默退回默认环境。`GET /api/v1/runtime-profiles` 遵循沙箱服务的认证。OpenSandbox 可使用命名 CPU 配置；当前适配器不能保证 GPU 分配，明确拒绝 GPU 配置。

## 环境绑定

解析器、wheel ABI 选择、Requires-Python、依赖 marker 和容器安装器使用同一目标版本。环境锁的旧默认 JSON 格式及摘要保持兼容；实际 Python 次版本与注册信息不符时安装失败。支持平台为 Linux x86_64、glibc 不低于 2.36，安装器再次核对实际版本和完整依赖闭包。

解析阶段目前按目标 `X.Y.0` 计算完整版本条件；实际安装器会按真实补丁版本复核，部分只适用于较新补丁版本的包可能被保守拒绝。解析前的实际运行时探测和自动切换仍待接入。

标准库识别不再取决于宿主机 Python。`sources/stdlib.json` 来自三个真实目标容器，保存实际 Python、平台、glibc、镜像 ID 和模块名称；移除的标准库不会被悄悄当作同名 PyPI 包补装。可用以下命令刷新元数据，实验依赖仍只在容器安装：

```powershell
uv run --locked python scripts/capture_stdlib.py
```

## 实际验收

- 真实 CPU 环境为 Python 3.11.17、3.12.15、3.13.16。7 项 Docker 用例覆盖直接与 HTTP 适配器、离线 wheel 安装、实际模块路径、退出码、清理，以及错标版本拒绝。该组测试包为 tests 内的受控夹具。
- 另有 3 项公开 PyPI/Docker 用例通过（45.02 秒）：各版本实际解析、下载并安装 PyYAML 6.0.3，加载对应 cp311/cp312/cp313 原生 `_yaml` 扩展，实际解析值为 41。
- 真实 RTX 3060 Laptop GPU 上完成 CUDA Driver/PTX 向量加法，结果为 `[11, 22, 33, 44]`，GPU 用例通过（7.80 秒）。代码只读取后上传容器执行，未在宿主机导入或执行。此项没有训练 Transformer，也没有 WMT/BLEU 结果。
- CPU 和 GPU 的独立现场回执位于忽略路径 `.data/parity-live/runtime-foundation-8a260b5071044eaf94c4847375bedf19/`，包含锁、实际环境、镜像 ID、代码摘要与真实输出。HTTP 采用本进程 ASGI 传输，并非新版本远程部署验收。
- 完整 Docker 回归为 42 项通过、14 项按外网/独立服务/GPU 开关跳过（281.13 秒）；上述 3 项外网及 1 项 GPU 验收另外执行。没有把跳过项记为通过。Compose 空配置/命名配置以及 wheel 内标准库资源均已核对。

本批仍在迁移开发分支，当前网页的最终集成部署待完成。全部剩余范围见 [迁移清单](implementation-plan.md)。
