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

## 验证与剩余范围

```powershell
$env:OPENRESEARCH_TEST_DOCKER='1'
uv run --locked pytest tests/acceptance/test_core_checkpoints.py -q
```

2026-10-21 的 14 项实际 Linux 容器测试通过（Windows Docker Desktop named pipe，62.84 秒）。覆盖文件/导出环境/cwd/函数恢复、Unicode 和引号、unset 不复活、真实退出码、空输出成功、长命令、LF/CRLF 原始字节、摘要、串行化、超时/取消/输出上限后的终端复用、主动退出、启动/提交期间取消、外来镜像拒绝、镜像/运行时创建失败以及 VOLUME 拒绝。每项关闭后所属容器和快照镜像均为零；测试中的延迟只控制取消时机，Docker 创建和提交仍实际执行。

Linux 宿主 Unix socket 路径由新提交 CI 继续验证。失败负反馈、Checker/Watchdog、断路器、超时回滚策略、策略竞速、加权 IO/GPU 门控仍待实现。本页证明独立终端与检查点能力，不表示完整 Go 功能迁移或最终应用部署完成。
