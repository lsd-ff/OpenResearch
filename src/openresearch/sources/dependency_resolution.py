"""只解析 wheel 元数据，不导入仓库、不运行构建脚本或安装实验依赖。"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from openresearch.distribution.resources import bundled_uv
from openresearch.errors import ContractError, DependencyResolutionError
from openresearch.models.interpreters import InterpreterIdentity
from openresearch.models.runtime_profiles import TargetPython
from openresearch.models.sources import EnvironmentSpec
from openresearch.sources.processes import close_process


def dependency_requests(values: list[str], *, limit: int = 32) -> list[str]:
    if len(values) > limit:
        raise ContractError(f"automatic environment exceeds {limit} dependency requests")
    requests = []
    for value in values:
        try:
            requirement = Requirement(value)
        except InvalidRequirement:
            raise ContractError("invalid automatic dependency request") from None
        if requirement.url or requirement.extras or requirement.marker or len(value) > 256:
            raise ContractError("automatic dependencies require package names and version ranges")
        requests.append(str(requirement))
    return requests


async def capped_output(stream: asyncio.StreamReader | None) -> bytes:
    if stream is None:
        raise ContractError("dependency resolver has no output stream")
    output = bytearray()
    while chunk := await stream.read(4096):
        if len(output) + len(chunk) > 65536:
            raise ContractError("dependency resolver output exceeds limit")
        output.extend(chunk)
    return bytes(output)


class DependencyResolver:
    def __init__(
        self, python: TargetPython = "3.12", *, interpreter: InterpreterIdentity | None = None
    ) -> None:
        self.python = python
        self.interpreter = interpreter
        if interpreter and interpreter.python != python:
            raise ContractError("resolver interpreter differs from its target Python")

    def for_interpreter(self, interpreter: InterpreterIdentity) -> "DependencyResolver":
        return (
            self
            if self.interpreter == interpreter
            else DependencyResolver(interpreter.python, interpreter=interpreter)
        )

    def for_python(self, python: TargetPython) -> "DependencyResolver":
        # Agent 实例可能被并行分支共享，不能修改当前解析器的目标版本。
        return self if python == self.python else DependencyResolver(python)

    async def resolve(self, requests: list[str]) -> EnvironmentSpec:
        return await self._resolve(requests, 32)

    async def resolve_recovery(self, requests: list[str]) -> EnvironmentSpec:
        # 模型仍最多提出 8 个变更；此入口只为保留已有最多 128 个锁定包，不能扩展新设计预算。
        return await self._resolve(requests, 128)

    async def _resolve(self, requests: list[str], limit: int) -> EnvironmentSpec:
        dependencies = dependency_requests(requests, limit=limit)
        if not dependencies:
            return EnvironmentSpec(interpreter=self.interpreter)
        executable = bundled_uv() or shutil.which("uv")
        if executable is None:
            raise ContractError("automatic dependency resolution requires the bundled uv runtime")
        with tempfile.TemporaryDirectory(prefix="openresearch-dependencies-") as directory:
            root = Path(directory)
            (root / "requirements.in").write_text("\n".join(dependencies), encoding="utf-8")
            await self._compile(executable, root)
            raw = (root / "requirements.txt").read_text(encoding="utf-8")
        # 解析器的输出仍须通过精确版本契约，后续下载再次校验元数据、闭包与 SHA。
        pins = [
            line.strip() for line in raw.splitlines() if line.strip() and not line.startswith("#")
        ]
        try:
            return EnvironmentSpec(requirements=pins, interpreter=self.interpreter)
        except ValueError:
            raise ContractError(
                "automatic dependency lock is unsupported or exceeds limits"
            ) from None

    async def _compile(self, executable: str, root: Path) -> None:
        # 不继承 API Key、用户 pip 配置或任意索引；no-build 禁止执行依赖构建代码。
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
        }
        environment.update(UV_HTTP_TIMEOUT="30", UV_NO_PROGRESS="1")
        creation = asyncio.create_task(
            asyncio.create_subprocess_exec(
                executable,
                "--no-config",
                "pip",
                "compile",
                str(root / "requirements.in"),
                "--output-file",
                str(root / "requirements.txt"),
                "--python-version",
                self.interpreter.python_full_version if self.interpreter else self.python,
                "--python-platform",
                "x86_64-manylinux_2_36",
                "--no-build",
                "--no-header",
                "--no-annotate",
                "--default-index",
                "https://pypi.org/simple",
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )
        try:
            # 与容器创建一样，启动窗口取消时仍须拿到真实进程句柄再清理。
            process = await asyncio.shield(creation)
        except asyncio.CancelledError:
            process = await creation
            await close_process(process)
            raise
        readers = [
            asyncio.create_task(capped_output(process.stdout)),
            asyncio.create_task(capped_output(process.stderr)),
        ]
        try:
            async with asyncio.timeout(90):
                stdout, stderr = await asyncio.gather(*readers)
                exit_code = await process.wait()
                if exit_code != 0:
                    raise DependencyResolutionError(
                        (stderr or stdout).decode("utf-8", errors="replace"), exit_code
                    )
        except TimeoutError:
            raise ContractError("automatic dependency resolution timed out") from None
        finally:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            await close_process(process)
