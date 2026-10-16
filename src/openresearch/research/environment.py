import asyncio
import json
from importlib import resources

from openresearch.config import Settings
from openresearch.errors import SandboxError
from openresearch.models.research import EnvironmentReport
from openresearch.models.sources import EnvironmentLock
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


async def install_environment(
    session: RunningSandbox, lock: EnvironmentLock, settings: Settings
) -> EnvironmentReport:
    await session.put_files(await environment_files(lock, settings))
    code = (
        resources.files("openresearch.sandbox.programs")
        .joinpath("install_environment.py")
        .read_text(encoding="utf-8")
    )
    result = await session.execute(code)
    if result.exit_code:
        raise SandboxError(f"offline dependency installation failed: {result.stderr[-2000:]}")
    raw = json.loads(await session.get_file("environment.json", 65536))
    report = EnvironmentReport(
        **raw,
        container_image=settings.sandbox_image,
        container_image_id=await session.adapter.image_identity(session.id),
    )
    expected = dict(wheel.requirement.split("==", 1) for wheel in lock.wheels)
    if report.installed != expected:
        raise SandboxError("installed dependency versions differ from the frozen environment lock")
    return report
