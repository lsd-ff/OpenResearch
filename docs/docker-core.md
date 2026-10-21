# 独立 Docker 持久终端与检查点

`openresearch.sandbox.core` 与 `openresearch.checkpoint` 对应 Go docker-core 的底层容器、TTY 和检查点职责，供本地管理代码调用。科研 Worker 继续使用原有执行适配器；这两个模块目前没有接入聊天工具或 HTTP API，也不负责智能体决策。

## 使用

镜像须已安装 Bash、env、stty 等基础工具，并存在于 Docker Engine。本模块按实际镜像 ID 创建容器，不隐式拉取镜像。先准备镜像及项目环境：

```powershell
uv sync --locked
docker pull python:3.12-slim
```

下例 Python 只在宿主编排 Docker 操作，命令均在容器中运行：

```python
import asyncio
from pathlib import Path

from openresearch.checkpoint.manager import CheckpointManager
from openresearch.sandbox.core.models import CoreConfig
from openresearch.sandbox.core.session import CoreSandbox


async def main():
    config = CoreConfig(data_dir=Path(".data/docker-core"), image="python:3.12-slim")
    async with CoreSandbox(config) as sandbox:
        checkpoints = CheckpointManager(sandbox)
        assert (await sandbox.execute("export VERSION=one; printf original > value")).succeeded
        snapshot = await checkpoints.commit("prepared")
        assert (await sandbox.execute("export VERSION=two; printf changed > value")).succeeded
        receipt = await checkpoints.rollback(snapshot.image_id)
        result = await sandbox.execute("printf '%s:' \"$VERSION\"; cat value")
        assert result.succeeded and result.output == "one:original"
        assert receipt.new_container_id != receipt.old_container_id
        print(result.output)


asyncio.run(main())
```

## 契约

- `CoreConfig` 默认 Ubuntu 22.04、runc、工作目录 `/workspace`、2 GiB 内存、2 CPU、128 个进程和每命令 300 秒。网络默认 `none`，管理员可明确选择 `bridge`；`runtime="runsc"` 等名称交给 Docker 验证，不可用就失败。当前这一层仅支持 CPU，GPU 加权门控仍在后续迁移范围。
- `CoreSandbox` 必须用 `async with` 或显式 `close()` 释放。每个会话持有实际 Bash TTY，命令、提交、回滚和关闭共享锁。命令之间保留环境、变量、函数和 cwd；stdin 连接 `/dev/null`，命令需要终端设置时可明确访问容器的 `/dev/tty`。
- `execute()` 返回真实退出码、合并 TTY 输出、首尾摘要和原始审计文件路径。空输出、退出码零可以成功。默认命令上限 64 KiB、输出上限 8 MiB；超过输出预算或超时时不能成功。TTY 不区分 stdout/stderr。
- 超时或任务取消时发送 Ctrl+C 并等待独立屏障，避免旧输出污染下一命令。终端不能恢复时抛出 `SandboxError`，退出上下文仍清理容器。任意命令自行退出 Bash 也属于连接失败。
- 原始输出按字节保存，不统一替换 CRLF/LF。摘要按 Unicode 行做首尾截断。取消、超时、输出上限和断开时的部分回执保存在数据目录，不把摘要当作完整输出。
- `CheckpointManager.commit(node_id)` 先在容器可写层保存导出环境、函数和 cwd，再执行带暂停的实际 Docker Commit。`rollback(image_id)`、`rollback_to_node(node_id)` 和 `rollback_to_latest()` 仅接受当前注册表、当前会话所属镜像；新容器就绪后才移除旧容器并裁剪后续记录。相同节点替换快照时保留其原注册顺序。
- Docker Commit 保存文件层，不保存进程内存和未导出的 shell 变量；工作目录、导出环境和函数通过容器内状态脚本恢复。[Docker 文档明确挂载卷不进入 Commit](https://docs.docker.com/reference/cli/docker/container/commit/)，因此本模块不接受宿主挂载，声明 VOLUME 的基础镜像会在创建容器前拒绝。
- 已进入 Docker 的提交/重建在等待者取消后仍完成事务，再释放锁并传播取消，避免后台线程越过锁修改状态。调用者应查注册表确认已完成的提交；不能仅凭取消认定操作未发生。
- 快照注册表属于本会话，关闭时移除所属容器和快照镜像。清理按独立命名空间及会话标签定位；原基础镜像、其他科研容器和其他会话不在清理范围内。命令、原始输出和导出环境属于本地私有运行数据，不提交 Git。

Bash 使用 [官方 `--noediting` 选项](https://www.gnu.org/s/bash/manual/html_node/Invoking-Bash.html) 关闭 Readline，并通过 `/dev/tty` 设置终端模式，避免行编辑恢复旧终端配置和命令 stdin 重定向导致设置无效。

## 故障审计与诊断

`openresearch.watchdog.service.Watchdog(sandbox, checkpoints=None, config=None, checker=None)` 包装同一个 `CoreSandbox`。不提供检查点管理器时创建空管理器；外部管理器必须绑定同一沙箱。`WatchdogConfig` 默认命令时限 300 秒、失败窗口 5，实际时限取沙箱预算和该配置的较小值。每次调用只执行一次原命令，诊断重试不重跑命令。

- `execute(command, node_id="command")` 成功返回原始 `CommandResult`；失败抛出携带结果和提示的 `WatchdogError`。原因包括 `ENV_CORRUPTED`、`TIMEOUT_HANG`、`OUTPUT_LIMIT`、`CIRCUIT_BROKEN`。
- Checker 优先拒绝超时、输出超量和崩溃信号，其余情况信任退出码。成功日志中的普通 error/failed 文字不进入失败窗口；真实失败的命令和输出摘要连续填满窗口时报告断路。取消继续传播，不记录为失败。断路器报告重复失败，调用方负责停止当前错误策略；它不承诺阻止此后显式调用。
- 默认诊断使用退出码及缺失命令/权限规则。可明确传入 `Checker(LLMAdvisor(LLMClient(settings)), checker_config)` 使用现有模型配置。命令/输出各截为 2,000 字符，历史上下文默认 16,000 字符，按不可信证据发送；建议只作为提示返回，不执行建议、不改写原失败结论。
- `CheckerConfig` 最多三次诊断尝试，单次默认 30 秒，失败记录异常类型并回退规则。一次结构化模型调用仍遵守 LLMClient 既有格式重试和请求总时限；最多三次指诊断调用层，不把内部请求忽略不计。取消不被吞为诊断失败。
- `CheckpointManager.record_failure_and_get_feedback()` 与 `get_full_feedback_context()` 保留有界失败片段、传入命令/输出文本的 SHA、数量及被省略记录数；默认最多 32 条。原始捕获字节的摘要在执行回执中，超量和连接中断仍是部分输出。新失败清除旧恢复标记，只有实际回滚回执才更新 `last_rollback`。控制字符的 JSON 转义也计入上下文预算，紧预算可只保留最近失败的摘要身份。
- `rollback_to_safe_state(image_id)` 显式调用真实检查点回滚，完成后返回回执。默认不会因失败或模型建议自行回滚。实际恢复后原命令仍保持失败；后续新命令是独立执行。

看门狗审计保存在 `<data_dir>/watchdog/*.json`，引用实际命令原始回执。终端无法在中断后恢复时，`TerminalRecoveryError` 携带实际超时/输出上限与部分输出；连接终止时 `TerminalConnectionError` 同样保留部分输出，退出码未知就保持 `None`。异常元数据不会覆盖已保存的实际标志。

## 加权资源队列

`openresearch.executor.gates` 提供 `WeightedSemaphore`、`Gatekeeper` 和 `ResourceConfig`。默认 IO 配额为 5、GPU 逻辑配额为 1；CPU 环境可明确设 `max_gpu_tasks=0`。这些值是调度预算，不探测物理设备，GPU 容器准入及策略调度接入仍待完成。

`acquire_io(weight)` / `acquire_gpu(weight)` 为异步获取，`release_io(weight)` / `release_gpu(weight)` 为同一事件循环内同步归还。推荐使用 `async with gates.reserve(io_weight=1, gpu_weight=0)`：先校验两个权重，再按 IO→GPU 顺序获取，后者等待时取消也会归还前者。零权重不占配额，负数、非整数、超过总容量和超额归还均拒绝。

FIFO 队首需要完整权重才能运行，后来的小请求不能抢占其可用份额。队首取消后会立即推进下一可满足请求；即使等待 Future 已获配额、任务尚未恢复就取消，也会归还预留份额。`io_usage`、`gpu_usage` 和单个信号量的 `usage` 提供不可变的容量/占用/等待快照。一个队列绑定首次使用的事件循环，不跨循环或跨线程共享。等待预算可由调用方的 `asyncio.timeout()` 覆盖。

12 项行为回归覆盖 FIFO、队首取消、获配后取消、异常释放、组合等待取消、CPU 零 GPU 配额、手动 API、无效权重/超额释放、跨循环拒绝及 50 个加权并发任务。它们验证调度原语，不把逻辑 GPU 份额当作真实设备测试。

## 验证与剩余范围

```powershell
$env:OPENRESEARCH_TEST_DOCKER='1'
uv run --locked pytest tests/acceptance/test_core_checkpoints.py tests/acceptance/test_core_watchdog.py -q
```

2026-10-21 的 14 项实际 Linux 容器测试通过（Windows Docker Desktop named pipe，62.84 秒）。覆盖文件/导出环境/cwd/函数恢复、Unicode 和引号、unset 不复活、真实退出码、空输出成功、长命令、LF/CRLF 原始字节、摘要、串行化、超时/取消/输出上限后的终端复用、主动退出、启动/提交期间取消、外来镜像拒绝、镜像/运行时创建失败以及 VOLUME 拒绝。每项关闭后所属容器和快照镜像均为零；测试中的延迟只控制取消时机，Docker 创建和提交仍实际执行。

后续终端/Watchdog 联合回归 21 项通过（112.32 秒），包括实际重复失败触发窗口、成功日志关键词不误判、普通超时和忽略 SIGINT 后超时的准确分类、取消、实际 SIGSEGV、输出上限、断开时部分输出和同命名空间双会话清理隔离；无显式管理器入口另经真实断开用例通过（4.31 秒）。终端/检查点初版 `4ae943f` 的 18 项远程检查已成功，含 Linux 宿主 Unix socket 路径；本批新功能以新 CI 为准。

2026-10-21 真实配置模型验收位于忽略路径 `.data/parity-live/watchdog-eac1bac7c14b48f5b2a65a58703a3c82/summary.json`。缺失命令实际退出 127，一次诊断尝试成功且没有把失败提升为成功；只有显式回滚后，新容器恢复导出环境和文件，输出 `stable:original`。关闭后所属容器与快照均为零。这是故障诊断/恢复功能验收，不是论文训练效果。

加入资源队列后的默认门禁为 821 项通过、2 项公共 PDF 按开关跳过，Ruff、严格 mypy（264 源文件）与构建通过。完整 Docker 回归 108 项通过、37 项按外网/独立服务/GPU 条件跳过（1065.63 秒），队列原语单独由上述 12 项行为测试覆盖。独立 CPython 3.12.13 环境也通过相同的 821 项默认测试、静态检查和构建，以及 21 项实际终端/Watchdog 容器回归；现有 3.13.5 环境未替换。

策略竞速、资源队列的执行器/GPU 实际接入及最终应用集成仍待完成，本页不表示完整 Go 功能迁移或最终部署完成。最新 GitHub CI 因账号限制未分配 runner、没有执行步骤，本地结果与未启动的远端作业分别记录。
