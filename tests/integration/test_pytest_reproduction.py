import ast
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from openresearch.agents.research_prepare import ResearchPreparer
from openresearch.errors import ContractError
from openresearch.models.context import Identity
from openresearch.models.research import BenchmarkSpec, FilePatch, ResearchPatch, ResearchSpec
from openresearch.models.sources import RepositorySpec
from openresearch.models.submissions import PlanSubmission
from openresearch.research.patches import apply_patch, pytest_launcher
from openresearch.services.plans import PlanService


def suite_plan(settings, repository):
    return PlanService(settings, repository).build(
        PlanSubmission(
            "复现核心架构并跑通仓库测试",
            Identity("alice", "pytest-suite"),
            workflow="reproduce",
            research=ResearchSpec(
                repository=RepositorySpec(url="https://github.com/example/model"),
                test_paths=["tests/test_model.py"],
            ),
        )
    )


async def test_existing_suite_gets_an_executable_launcher_without_model_code_generation(
    settings, repository, files
):
    plan = suite_plan(settings, repository)
    task = next(node for node in plan.nodes if node.type == "research_prepare")
    preparer = ResearchPreparer(settings, files)
    sources = {"tests/test_model.py": b"def test_model():\n    assert 2 + 2 == 4\n"}
    preparer.workspace = lambda _: (None, sources)
    preparer.llm.request_typed = AsyncMock(side_effect=AssertionError("must reuse the real suite"))
    result = await preparer.execute(plan, task)
    patch = ResearchPatch.model_validate_json(result.outputs["research_patch"])
    spec = ResearchSpec.model_validate(task.inputs["research"])
    merged = apply_patch(sources, patch, spec)
    assert merged["tests/test_model.py"] == sources["tests/test_model.py"]
    assert b"raise SystemExit(pytest.main(" in merged[spec.entrypoint]
    assert b"tests/test_model.py" in merged[spec.entrypoint]
    assert preparer.llm.request_typed.call_count == 0


async def test_missing_original_suite_is_rejected_instead_of_generating_replacement_tests(
    settings, repository, files
):
    plan = suite_plan(settings, repository)
    task = next(node for node in plan.nodes if node.type == "research_prepare")
    preparer = ResearchPreparer(settings, files)
    preparer.workspace = lambda _: (None, {"model.py": b"class Model: pass"})
    with pytest.raises(ContractError, match="pytest source does not exist"):
        await preparer.execute(plan, task)


@pytest.mark.parametrize(
    "replacement", ["print('18 passed')", "import pytest\npytest.main(['-k', 'easy'])"]
)
def test_repairs_cannot_replace_the_launcher_or_filter_out_original_tests(replacement):
    spec = ResearchSpec(test_paths=["test_model.py"])
    sources = {
        "test_model.py": b"def test_core(): assert False",
        spec.entrypoint: pytest_launcher(spec.test_paths).encode(),
    }
    patch = ResearchPatch(
        files=[
            FilePatch(
                path=spec.entrypoint,
                base_sha256=hashlib.sha256(sources[spec.entrypoint]).hexdigest(),
                content=replacement,
            )
        ]
    )
    with pytest.raises(ContractError, match="must not be replaced or filtered"):
        apply_patch(sources, patch, spec)


@pytest.mark.parametrize("path", ["../test.py", "/test.py", "test_model.py::test_easy", "test.sh"])
def test_suite_paths_cannot_escape_or_select_only_individual_cases(path):
    with pytest.raises((ValidationError, ContractError)):
        ResearchSpec(test_paths=[path])


def test_original_test_sources_cannot_be_authorized_patch_paths():
    with pytest.raises(ValidationError, match="must remain unmodified"):
        ResearchSpec(
            test_paths=["test_model.py"], patch_paths=["openresearch_run.py", "test_model.py"]
        )


def test_test_filename_cannot_be_interpreted_as_a_pytest_filter_option():
    tree = ast.parse(pytest_launcher(["-k.py"]))
    invocation = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "main"
    )
    arguments = ast.literal_eval(invocation.args[0])
    assert arguments[arguments.index("--") + 1 :] == ["-k.py"]


def test_existing_tests_cannot_silently_replace_a_predictive_benchmark():
    benchmark = BenchmarkSpec(upload_id="dataset", input_columns=["x"], target_column="label")
    with pytest.raises(ValidationError, match="cannot replace benchmarks"):
        ResearchSpec(test_paths=["test_model.py"], benchmark=benchmark)


async def test_designer_locks_pytest_and_preserves_actual_suite_paths(settings, repository, files):
    from openresearch.agents.research_design import ResearchDesigner
    from openresearch.models.planning import ExperimentDesign
    from openresearch.models.sources import EnvironmentSpec

    plan = suite_plan(settings, repository)
    task = next(node for node in plan.nodes if node.type == "research_prepare")
    designer = ResearchDesigner(settings, files)
    designer.preparer.workspace = lambda _: (
        None,
        {
            "tests/test_model.py": b"from model import forward\ndef test_core(): assert forward()",
            "model.py": b"def forward(): return 1",
        },
    )
    designer.llm.request_typed = AsyncMock(
        return_value=ExperimentDesign(
            test_paths=["model_suggested_other_test.py"],
            patch_paths=["openresearch_run.py", "model.py"],
            dependencies=["numpy>=1.26"],
            rationale="Run the original suite",
        )
    )
    designer.dependencies.resolve = AsyncMock(return_value=EnvironmentSpec())
    task.type = "research_design"
    task.contract.allowed_tools = ["research.design"]
    result = await designer.execute(plan, task)
    resolved = json.loads(result.outputs["research_spec"])
    assert resolved["test_paths"] == ["tests/test_model.py"]
    assert resolved["patch_paths"] == ["openresearch_run.py", "model.py"]
    context = json.loads(designer.llm.request_typed.call_args.args[0][1]["content"])
    assert context["pytest_implementation_candidates"] == ["model.py"]
    assert designer.dependencies.resolve.call_args.args[0] == ["numpy>=1.26", "pytest>=7,<10"]
