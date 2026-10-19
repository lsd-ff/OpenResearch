import json

import httpx
import pytest
from pydantic import SecretStr

from openresearch.agents.research_design import ResearchDesigner
from openresearch.agents.research_prepare import ResearchPreparer
from openresearch.errors import PlanningInputRequired
from openresearch.llm.client import LLMClient
from openresearch.models.context import Identity
from openresearch.models.documents import SourceIndex, SourceLink
from openresearch.models.execution import ExecutionLease
from openresearch.models.planning import PendingPlanning
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Status, new_id
from openresearch.models.sources import EnvironmentSpec, RepositorySpec
from openresearch.models.submissions import PlanSubmission
from openresearch.scheduler.executor import TaskExecutor
from openresearch.services.plans import PlanService
from openresearch.sources.dependency_resolution import DependencyResolver, dependency_requests


async def test_generated_design_artifact_drives_preparation(settings, repository, files):
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "run a real source adapter",
            Identity("alice", "design"),
            workflow="reproduce",
            research=ResearchSpec(
                repository=RepositorySpec(url="https://github.com/pallets/itsdangerous")
            ),
            auto_design=True,
        )
    )
    task = next(node for node in plan.nodes if node.type == "research_design")
    designer = ResearchDesigner(settings, files)
    settings.llm_api_key = SecretStr("private-token")
    settings.llm_model = "test-model"

    def model(request):
        body = json.loads(request.content)
        assert "real source adapter" in body["messages"][-1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "entrypoint": "adapter.py",
                                    "patch_paths": ["adapter.py"],
                                    "dependencies": ["blinker>=1.9,<2"],
                                    "rationale": "Use a source adapter",
                                }
                            )
                        }
                    }
                ]
            },
        )

    async def resolve(requests):
        assert requests == ["blinker>=1.9,<2"]
        return EnvironmentSpec(requirements=["blinker==1.9.0"])

    designer.llm = LLMClient(settings, httpx.MockTransport(model))
    designer.dependencies.resolve = resolve
    result = await designer.execute(plan, task)
    resolved = ResearchSpec.model_validate_json(result.outputs["research_spec"])
    assert resolved.entrypoint == "adapter.py"
    lease = ExecutionLease(plan.id, task.id, new_id(), "test")
    plan.artifacts["research_spec"] = files.write(
        lease, "research_spec", result.outputs["research_spec"]
    )
    prepare = next(node for node in plan.nodes if node.type == "research_prepare")
    prepare.inputs["code"] = "print('actual adapter')"
    patch = await ResearchPreparer(settings, files).execute(plan, prepare)
    assert json.loads(patch.outputs["research_patch"])["files"][0]["path"] == "adapter.py"


async def test_design_corrects_diff_filenames_before_dependency_resolution(
    settings, repository, files
):
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "run core tests",
            Identity("alice", "design-correction"),
            workflow="reproduce",
            research=ResearchSpec(
                repository=RepositorySpec(url="https://github.com/pallets/itsdangerous")
            ),
            auto_design=True,
        )
    )
    task = next(node for node in plan.nodes if node.type == "research_design")
    settings.llm_api_key = SecretStr("private-token")
    settings.llm_model = "test-model"
    calls = []

    def model(request):
        calls.append(json.loads(request.content))
        paths = ["patches/fix-tests.patch"] if len(calls) == 1 else ["openresearch_run.py"]
        value = {"patch_paths": paths, "rationale": "Execute an actual test adapter"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}}]})

    designer = ResearchDesigner(settings, files)
    designer.llm = LLMClient(settings, httpx.MockTransport(model))
    result = await designer.execute(plan, task)
    assert len(calls) == 2
    assert "patch_paths" in calls[-1]["messages"][-1]["content"]
    spec = ResearchSpec.model_validate_json(result.outputs["research_spec"])
    assert spec.patch_paths == ["openresearch_run.py"]


def test_empty_generated_patch_paths_include_the_validated_adapter():
    from openresearch.models.planning import ExperimentDesign

    design = ExperimentDesign(entrypoint="adapter.py", patch_paths=[], rationale="run actual tests")
    assert design.patch_paths == ["adapter.py"]


async def test_patch_generation_receives_the_original_goal_as_well_as_the_task(
    settings, repository, files, monkeypatch
):
    from openresearch.models.research import FilePatch, ResearchPatch

    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "verify causal mask and real loss decrease",
            Identity("alice", "original-goal"),
            workflow="reproduce",
            research=ResearchSpec(
                repository=RepositorySpec(url="https://github.com/pallets/itsdangerous")
            ),
        )
    )
    task = next(node for node in plan.nodes if node.type == "research_prepare")
    task.description = "prepare adapter"
    captured = []

    async def propose(self, intent, spec, sources, error=""):
        captured.append(intent)
        return ResearchPatch(files=[FilePatch(path=spec.entrypoint, content="print('actual')")])

    monkeypatch.setattr(ResearchPreparer, "propose", propose)
    await ResearchPreparer(settings, files).execute(plan, task)
    assert plan.user_intent in captured[0] and "prepare adapter" in captured[0]


async def test_worker_material_question_resumes_current_plan_only(
    settings, repository, files, sandbox
):
    service = PlanService(settings, repository)
    identity = Identity("alice", "worker-materials")
    plan = service.create(PlanSubmission("smoke", identity))
    repository.queue_plan(plan.id, plan.owner_id)
    claimed_task = repository.claim_next(
        "worker", settings.lease_seconds, settings.max_concurrent_tasks
    )
    assert claimed_task is not None
    claimed, task = claimed_task
    lease = ExecutionLease.for_task(claimed, task, "worker")
    executor = TaskExecutor(settings, repository, files, sandbox)
    pending = PendingPlanning(intent="复现论文", question="请补充真实仓库链接。")
    await executor._fail(lease, PlanningInputRequired(pending))
    assert repository.get_plan(plan.id).status == Status.FAILED
    assert repository.sessions.get(identity).pending_planning == pending
    newer = service.create(PlanSubmission("smoke", identity))
    repository.sessions.require_materials(plan.id, PendingPlanning(intent="older", question="old"))
    assert repository.sessions.get(identity).plans[-1] == newer.id
    assert repository.sessions.get(identity).pending_planning is None


async def test_paper_repository_ambiguity_uses_real_candidates(settings, files):
    from openresearch.agents.repository import RepositoryAgent
    from openresearch.models.runtime import Plan, Task

    plan = Plan(
        user_intent="复现论文",
        owner_id="alice",
        session_id="paper",
        nodes=[Task(name="repository", type="repository_fetch", assigned_to="research_agent")],
    )
    urls = ["https://github.com/pallets/itsdangerous", "https://github.com/pallets/blinker"]
    index = SourceIndex(links=[SourceLink(upload_id="paper", page=1, url=url) for url in urls])
    lease = ExecutionLease(plan.id, plan.nodes[0].id, new_id(), "test")
    plan.artifacts["source_index"] = files.write(lease, "source_index", index.model_dump_json())
    settings.llm_api_key = SecretStr("private-token")
    settings.llm_model = "test-model"
    agent = RepositoryAgent(settings, files)
    agent.llm = LLMClient(
        settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"choice":null,"reason":"two plausible repositories"}'
                            }
                        }
                    ]
                },
            )
        ),
    )
    with pytest.raises(PlanningInputRequired) as captured:
        await agent._discover(plan)
    assert [item.url for item in captured.value.pending.repository_candidates] == urls
    assert "回复编号" in captured.value.pending.question


def test_worker_question_preserves_withheld_benchmark_reference():
    from openresearch.agents.repository import RepositoryAgent
    from openresearch.models.runtime import Plan, Task

    task = Task(
        name="design",
        type="research_design",
        assigned_to="research_agent",
        inputs={
            "research": {
                "benchmark": {"upload_id": "dataset", "input_columns": ["x"], "target_column": "y"}
            }
        },
    )
    plan = Plan(
        user_intent="复现并评测",
        owner_id="alice",
        session_id="paper",
        attachments=["paper"],
        nodes=[task],
    )
    with pytest.raises(PlanningInputRequired) as captured:
        RepositoryAgent._ask(plan, [], "请补充仓库")
    assert captured.value.pending.attachments == ["paper", "dataset"]


@pytest.mark.parametrize(
    "dependency",
    [
        "pkg @ https://bad.example/code.whl",
        "pkg[extra]",
        "pkg; os_name=='nt'",
        "--index-url https://bad.example",
    ],
)
def test_automatic_dependencies_reject_executable_or_alternate_sources(dependency):
    from openresearch.errors import ContractError

    with pytest.raises(ContractError):
        dependency_requests([dependency])


async def test_dependency_resolver_uses_pinned_output_and_no_private_environment(monkeypatch):
    import asyncio
    from pathlib import Path

    observed = {}
    monkeypatch.setenv("OPENRESEARCH_LLM_API_KEY", "private-token")
    monkeypatch.setenv("UV_INDEX_URL", "https://private-token@bad.example/simple")
    monkeypatch.setattr("shutil.which", lambda _: "trusted-uv")

    class Process:
        returncode = 0
        stdout = asyncio.StreamReader()
        stderr = asyncio.StreamReader()

        async def wait(self):
            return 0

    async def start(*args, **options):
        observed["args"] = args
        observed["env"] = options["env"]
        await asyncio.to_thread(
            Path(args[args.index("--output-file") + 1]).write_text,
            "blinker==1.9.0\n",
            encoding="utf-8",
        )
        Process.stdout.feed_eof()
        Process.stderr.feed_eof()
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    assert (await DependencyResolver().resolve(["blinker>=1.9,<2"])).requirements == [
        "blinker==1.9.0"
    ]
    assert "--no-build" in observed["args"] and "--no-config" in observed["args"]
    assert (
        "OPENRESEARCH_LLM_API_KEY" not in observed["env"] and "UV_INDEX_URL" not in observed["env"]
    )
