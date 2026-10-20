import asyncio
import hashlib
import json
import time
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
    required_modules: tuple[str, ...] = ()
    framework_source_sha256: str | None = None


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
        + (
            f"required_roots = {json.dumps(list(request.required_modules))}\n"
            "calls = {root: set() for root in required_roots}\n"
            "def record_call(frame, event, arg):\n"
            "    if event != 'call' or frame.f_code.co_name == '<module>':\n"
            "        return\n"
            "    module = frame.f_globals.get('__name__', '')\n"
            "    if not frame.f_code.co_filename.startswith('/workspace/environment/'):\n"
            "        return\n"
            "    caller = frame.f_back\n"
            "    invoked = False\n"
            "    for _ in range(64):\n"
            "        if caller is None:\n"
            "            break\n"
            "        path = caller.f_code.co_filename\n"
            "        if path.startswith('<frozen importlib'):\n"
            "            break\n"
            "        if os.path.abspath(path) == os.path.abspath(sys.argv[0]):\n"
            "            invoked = True\n"
            "            break\n"
            "        caller = caller.f_back\n"
            "    if not invoked:\n"
            "        return\n"
            "    for root in required_roots:\n"
            "        matches = module == root or module.startswith(root + '.')\n"
            "        key = frame.f_code.co_name\n"
            "        important = key in "
            "{'retrieve', 'similarity_search', 'add_documents', 'query'}\n"
            "        if matches and len(calls[root]) < (64 if important else 32):\n"
            "            calls[root].add(module + '.' + frame.f_code.co_name)\n"
            "sys.setprofile(record_call)\n"
            "try:\n"
            "    runpy.run_path(sys.argv[0], run_name='__main__')\n"
            "finally:\n"
            "    sys.setprofile(None)\n"
            "    import json\n"
            "    with open('/tmp/openresearch/framework_trace.json', 'w') as trace:\n"
            "        json.dump({key: sorted(value) for key, value in calls.items()}, trace)\n"
            if request.required_modules
            else "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
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
            started = time.monotonic()
            result = await session.execute(entrypoint_program(request))
            elapsed = time.monotonic() - started
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
                execution_seconds=elapsed,
            )
            predictions: list[dict[str, object]] = []
            if not result.exit_code and request.dataset and request.spec.benchmark:
                try:
                    if request.required_modules:
                        trace = json.loads(await session.get_file("framework_trace.json", 65536))
                        if not isinstance(trace, dict) or any(
                            not isinstance(trace.get(root), list)
                            or not trace[root]
                            or any(not isinstance(item, str) for item in trace[root])
                            for root in request.required_modules
                        ):
                            raise ContractError("actual framework function calls were not observed")
                        run.module_calls = {root: trace[root] for root in request.required_modules}
                    raw = await session.get_file("predictions.json", self.settings.max_output_bytes)
                    predictions = json.loads(raw)
                    run.metrics = await asyncio.to_thread(
                        evaluate, request.dataset, predictions, request.spec.benchmark
                    )
                    run.predictions_sha256 = hashlib.sha256(raw).hexdigest()
                    if request.required_modules:
                        run.metrics.update(framework_module_usage=1.0, prediction_coverage=1.0)
                except (ContractError, SandboxError, json.JSONDecodeError) as exc:
                    attempt.error = str(exc)
                    predictions = []
            return ExperimentOutput(run, predictions)
