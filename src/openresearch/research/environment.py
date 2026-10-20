import asyncio
import json
from importlib import resources

from openresearch.config import Settings
from openresearch.errors import DependencyInstallError, SandboxError
from openresearch.models.research import EnvironmentReport
from openresearch.models.runtime_profiles import TargetPython
from openresearch.models.sources import EnvironmentLock
from openresearch.sandbox.profiles import selected_profile
from openresearch.sandbox.session import RunningSandbox
from openresearch.sources.blobs import SourceBlobs


async def environment_files(lock: EnvironmentLock, settings: Settings) -> dict[str, bytes]:
    blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)
    files = {
        f"wheels/{wheel.filename}": await asyncio.to_thread(blobs.read, wheel.sha256, wheel.size)
        for wheel in lock.wheels
    }
    requirements = "\n".join(
        f"{wheel.requirement} --hash=sha256:{wheel.sha256}" for wheel in lock.wheels
    )
    files["requirements.lock"] = requirements.encode()
    return files


def installation_program(python: TargetPython = "3.12") -> str:
    """生产与独立验收使用同一可信脚本，闭包校验不能依赖调用方临时注入。"""
    code = (
        resources.files("openresearch.sandbox.programs")
        .joinpath("install_environment.py")
        .read_text(encoding="utf-8")
    )
    closure = (
        resources.files("openresearch.sources")
        .joinpath("dependency_closure.py")
        .read_text(encoding="utf-8")
    )
    # 复用可信元数据算法；容器使用 pip 自带的 packaging，无需在线安装校验器。
    closure = closure.replace("from packaging.", "from pip._vendor.packaging.")
    expected = tuple(int(part) for part in python.split("."))
    return f"OPENRESEARCH_EXPECTED_PYTHON = {expected!r}\n" + closure + "\n" + code


async def install_environment(
    session: RunningSandbox, lock: EnvironmentLock, settings: Settings
) -> EnvironmentReport:
    profile = selected_profile(settings, session.runtime_profile)
    if profile and profile.python != lock.python:
        raise SandboxError("runtime profile Python differs from the frozen environment lock")
    await session.put_files(await environment_files(lock, settings))
    result = await session.execute(installation_program(lock.python))
    if result.exit_code:
        raise DependencyInstallError(result)
    raw = json.loads(await session.get_file("environment.json", 65536))
    report = EnvironmentReport(
        **raw,
        container_image=profile.image if profile else settings.sandbox_image,
        container_image_id=await session.adapter.image_identity(session.id),
    )
    expected = dict(wheel.requirement.split("==", 1) for wheel in lock.wheels)
    if report.installed != expected:
        raise SandboxError("installed dependency versions differ from the frozen environment lock")
    return report
