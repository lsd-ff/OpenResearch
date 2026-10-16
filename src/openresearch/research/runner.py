import asyncio
import hashlib
import json
from dataclasses import dataclass

from openresearch.config import Settings
from openresearch.errors import ContractError, SandboxError
from openresearch.models.execution import ExecutionLease
from openresearch.models.research import (
    ExperimentVariant,
    ResearchPatch,
    ResearchRun,
    ResearchSpec,
    RunAttempt,
)
from openresearch.models.sources import EnvironmentLock
from openresearch.research.benchmark import Dataset, evaluate
from openresearch.research.environment import install_environment
from openresearch.sandbox.session import SandboxSessions


@dataclass(frozen=True)
class ExperimentInput:
    lease: ExecutionLease
    spec: ResearchSpec
    variant: ExperimentVariant
    files: dict[str, bytes]
    patch: ResearchPatch
    lock: EnvironmentLock
    dataset: Dataset | None
    number: int


@dataclass(frozen=True)
class ExperimentOutput:
    run: ResearchRun
    predictions: list[dict[str, object]]


def entrypoint_program(request: ExperimentInput) -> str:
    arguments = json.dumps([request.spec.entrypoint, *request.variant.arguments])
    return (
        "import os, runpy, sys\n"
        "os.chdir('/tmp/openresearch/repository')\n"
        "sys.path[:0] = ['/workspace/environment', os.getcwd()]\n"
        f"sys.argv = {arguments}\n"
        "os.environ['OPENRESEARCH_INPUT'] = '/tmp/openresearch/benchmark_input.json'\n"
        "os.environ['OPENRESEARCH_PREDICTIONS'] = '/tmp/openresearch/predictions.json'\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )


class ExperimentRunner:
    def __init__(self, settings: Settings, sessions: SandboxSessions) -> None:
        self.settings = settings
        self.sessions = sessions

    async def run(self, request: ExperimentInput) -> ExperimentOutput:
        async with self.sessions.open(request.lease) as session:
            environment = await install_environment(session, request.lock, self.settings)
            files = {f"repository/{path}": raw for path, raw in request.files.items()}
            if request.dataset:
                files["benchmark_input.json"] = json.dumps(
                    request.dataset.inputs, ensure_ascii=False
                ).encode()
            await session.put_files(files)
            result = await session.execute(entrypoint_program(request))
            attempt = RunAttempt(
                number=request.number,
                patch_sha256=hashlib.sha256(request.patch.model_dump_json().encode()).hexdigest(),
                patch=request.patch,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                error=f"experiment exited with status {result.exit_code}"
                if result.exit_code
                else "",
            )
            run = ResearchRun(
                variant=request.variant.name,
                arguments=request.variant.arguments,
                attempts=[attempt],
                environment=environment,
            )
            predictions: list[dict[str, object]] = []
            if not result.exit_code and request.dataset and request.spec.benchmark:
                try:
                    raw = await session.get_file("predictions.json", self.settings.max_output_bytes)
                    predictions = json.loads(raw)
                    run.metrics = await asyncio.to_thread(
                        evaluate, request.dataset, predictions, request.spec.benchmark
                    )
                    run.predictions_sha256 = hashlib.sha256(raw).hexdigest()
                except (ContractError, SandboxError, json.JSONDecodeError) as exc:
                    attempt.error = str(exc)
                    predictions = []
            return ExperimentOutput(run, predictions)
