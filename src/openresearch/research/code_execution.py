from dataclasses import dataclass

from openresearch.config import Settings
from openresearch.errors import ContractError, DependencyInstallError, SandboxError
from openresearch.models.execution import ExecutionLease
from openresearch.models.output_files import OutputFiles
from openresearch.models.recovery import CodeAttempt
from openresearch.models.runtime import ExecutionResult
from openresearch.models.sources import EnvironmentLock
from openresearch.research.code_guards import code_digest
from openresearch.research.environment import install_environment
from openresearch.sandbox.collect import collect_files
from openresearch.sandbox.session import SandboxSessions


@dataclass(frozen=True)
class ProgramOutcome:
    attempt: CodeAttempt
    files: OutputFiles
    output: ExecutionResult | None


class CodeProgramRunner:
    def __init__(self, settings: Settings, sessions: SandboxSessions) -> None:
        self.settings, self.sessions = settings, sessions

    async def run(
        self, lease: ExecutionLease, code: str, lock: EnvironmentLock, number: int
    ) -> ProgramOutcome:
        attempt = CodeAttempt(
            number=number,
            phase="install",
            code=code,
            code_sha256=code_digest(code),
            environment_lock=lock,
        )
        output = None
        bundle = OutputFiles()
        try:
            async with self.sessions.open(lease) as session:
                attempt.sandbox_id = session.id
                attempt.container_image_id = await session.adapter.image_identity(session.id)
                if lock.wheels:
                    attempt.environment = await install_environment(session, lock, self.settings)
                attempt.phase = "execute"
                await session.put_files({"code.py": code.encode()})
                attempt.execution_requested = True
                output = await session.execute(
                    "import os, runpy, sys\n"
                    "sys.path[:0] = ['/workspace/environment', '/tmp/openresearch']\n"
                    "runpy.run_path('/tmp/openresearch/code.py', run_name='__main__')\n"
                )
                if output.exit_code:
                    attempt.error = (
                        f"Python exited with code {output.exit_code}: {output.stderr[-8000:]}"
                    )
                else:
                    attempt.phase = "outputs"
                    bundle = await collect_files(session, self.settings.max_output_bytes)
        except DependencyInstallError as exc:
            output, attempt.error = exc.result, str(exc)
        except (SandboxError, ContractError) as exc:
            attempt.error = str(exc)
        if output is not None:
            attempt.output_truncated = (
                len(output.stdout.encode()) > 16000 or len(output.stderr.encode()) > 16000
            )
            attempt.output = output.model_copy(
                update={"stdout": output.stdout[-16000:], "stderr": output.stderr[-16000:]}
            )
        return ProgramOutcome(attempt, bundle, output)
