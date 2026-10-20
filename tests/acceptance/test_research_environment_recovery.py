import hashlib
import io
import json
import os
import zipfile

import httpx
import pytest
from test_benchmark_preflight import COUNTED, DATA
from test_docker_smoke import pytestmark as pytestmark
from test_research import PREDICTOR, benchmark_plan, finish

from openresearch.models.environment_repair import EnvironmentRepairProposal
from openresearch.models.research import FilePatch, ResearchPatch
from openresearch.models.runtime import Status
from openresearch.models.sources import EnvironmentSpec
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker
from openresearch.sources.wheels import WheelSource


def fixture_wheel(name, module, source, requires=()):
    output = io.BytesIO()
    normalized = name.replace("-", "_")
    with zipfile.ZipFile(output, "w") as archive:
        prefix = normalized + "-1.0.dist-info/"
        archive.writestr(
            prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        archive.writestr(
            prefix + "METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
            + "".join("Requires-Dist: " + value + "\n" for value in requires),
        )
        archive.writestr(prefix + "RECORD", "")
        archive.writestr(module + "/__init__.py", source)
    return output.getvalue()


@pytest.mark.parametrize("repair", [True, False, "then_patch"])
async def test_real_install_failure_is_diagnosed_then_new_environment_executes_benchmark(
    settings, repository, files, monkeypatch, repair
):
    # 包来源、解析结果和修复提议为受控夹具；安装失败、安装校验、程序和指标均实际执行。
    requirement = "recovery-helper; python_full_version >= '3.12.1'"
    assets = {
        "recovery-probe": fixture_wheel(
            "recovery-probe", "recovery_probe", "from recovery_helper import value\n", [requirement]
        ),
        "recovery-helper": fixture_wheel("recovery-helper", "recovery_helper", "value = 31\n"),
    }

    def source_provider(request):
        if request.url.host == "pypi.org":
            name = request.url.path.split("/")[2]
            raw = assets[name]
            return httpx.Response(
                200,
                json={
                    "info": {
                        "name": name,
                        "version": "1.0",
                        "requires_python": ">=3.12",
                        "requires_dist": [requirement] if name == "recovery-probe" else [],
                    },
                    "urls": [
                        {
                            "filename": name.replace("-", "_") + "-1.0-py3-none-any.whl",
                            "url": "https://files.pythonhosted.org/" + name,
                            "packagetype": "bdist_wheel",
                            "yanked": False,
                            "size": len(raw),
                            "digests": {"sha256": hashlib.sha256(raw).hexdigest()},
                        }
                    ],
                },
            )
        return httpx.Response(200, content=assets[request.url.path.removeprefix("/")])

    source = WheelSource(settings, httpx.MockTransport(source_provider))
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    worker.executor.agents.coder.environment.source = source
    repairer = worker.executor.research_executor.repairs.environment_repairer
    repairer.dependencies.source = source

    async def propose(intent, request, failed):
        assert failed.phase == "install" and "unsatisfied dependency" in failed.stderr
        return EnvironmentRepairProposal(
            action="dependencies",
            reason="actual patch-version marker",
            requirements=["recovery-helper==1.0"],
        )

    async def resolve(values):
        assert "recovery-helper==1.0" in values
        return EnvironmentSpec(requirements=["recovery-probe==1.0", "recovery-helper==1.0"])

    monkeypatch.setattr(repairer, "propose", propose)
    monkeypatch.setattr(repairer.dependencies.resolver, "resolve", resolve)
    code = (
        "import recovery_probe\nassert recovery_probe.value == 31\n"
        "print('ACTUAL_HELPER', recovery_probe.value)\n" + PREDICTOR
    )
    if repair == "then_patch":
        code = code.replace(
            "print('ACTUAL_HELPER', recovery_probe.value)",
            "print('ACTUAL_HELPER', recovery_probe.absent_value)",
        )

        async def patch_code(intent, spec, sources, error):
            context = json.loads(error)
            assert "AttributeError" in context["stderr"]
            assert set(context["effective_environment"]) == {
                "recovery-probe==1.0",
                "recovery-helper==1.0",
            }
            return ResearchPatch(
                files=[
                    FilePatch(
                        path=spec.entrypoint,
                        base_sha256=hashlib.sha256(sources[spec.entrypoint]).hexdigest(),
                        content=code.replace("recovery_probe.absent_value", "recovery_probe.value"),
                    )
                ]
            )

        monkeypatch.setattr(worker.executor.research_executor.preparer, "propose", patch_code)
    plan = benchmark_plan(
        settings,
        repository,
        code=code,
        repairs=2 if repair == "then_patch" else int(repair),
        ablations=False,
        data=DATA,
        environment=EnvironmentSpec(requirements=["recovery-probe==1.0"]),
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        if not repair:
            assert done.status == Status.FAILED
            checked = json.loads(files.read(done.artifacts["benchmark_preflight_report"]))
            assert checked["status"] == "failed" and checked["run"]["environment"] is None
            assert len(checked["run"]["attempts"]) == 1
            assert checked["run"]["attempts"][0]["phase"] == "install"
            assert "ACTUAL_HELPER" not in checked["run"]["attempts"][0]["stdout"]
            assert not checked["environment_changes"] and "research_report" not in done.artifacts
            assert not repository.cleanup_candidates("docker")
            return
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        report = json.loads(files.read(done.artifacts["research_report"]))
        checked = report["preflight"]
        attempts = checked["run"]["attempts"]
        assert [item["phase"] for item in attempts] == (
            ["install", "execute", "execute"] if repair == "then_patch" else ["install", "execute"]
        )
        assert attempts[0]["exit_code"] != 0 and "unsatisfied dependency" in attempts[0]["stderr"]
        assert len({item["sandbox_id"] for item in attempts}) == len(attempts)
        if repair == "then_patch":
            assert "AttributeError" in attempts[1]["stderr"]
            assert attempts[0]["patch_sha256"] != attempts[-1]["patch_sha256"]
        assert checked["environment_changes"][0]["accepted"]
        assert "ACTUAL_HELPER 31" in attempts[-1]["stdout"]
        assert report["runs"][0]["metrics"]["accuracy"] == 1.0
        assert report["runs"][0]["environment"]["installed"] == {
            "recovery-probe": "1.0",
            "recovery-helper": "1.0",
        }
        assert report["environment_lock_sha256"] == checked["environment_lock_sha256"]
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()


@pytest.mark.external
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="PyPI opt-in required")
@pytest.mark.parametrize("stage", ["preflight", "full"])
async def test_real_pypi_missing_module_relocks_environment_and_rechecks_if_full(
    settings, repository, files, stage
):
    predictor = PREDICTOR.replace("print('real predictions written')", COUNTED)
    probe = (
        "import yaml\nvalue = yaml.safe_load('number: 9')\nassert value['number'] == 9\n"
        "print('ACTUAL_YAML', yaml.__version__, yaml.__file__, value['number'])\n"
    )
    code = predictor + (
        probe
        if stage == "preflight"
        else "if len(rows) > 8:\n" + "".join("    " + line + "\n" for line in probe.splitlines())
    )
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    plan = benchmark_plan(
        settings,
        repository,
        code=code,
        repairs=1,
        ablations=False,
        data=DATA,
        intent="使用 PyYAML==6.0.3 评测全部数据，保持程序与所有断言",
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        report = json.loads(files.read(done.artifacts["research_report"]))
        changed = report["preflight"] if stage == "preflight" else report
        attempts = (
            changed["run"]["attempts"] if stage == "preflight" else report["runs"][0]["attempts"]
        )
        assert len(attempts) == 2 and "ModuleNotFoundError" in attempts[0]["stderr"]
        assert attempts[0]["patch_sha256"] == attempts[-1]["patch_sha256"]
        assert attempts[0]["environment_lock_sha256"] != attempts[-1]["environment_lock_sha256"]
        assert attempts[0]["sandbox_id"] != attempts[-1]["sandbox_id"]
        assert changed["environment_changes"][0]["accepted"]
        assert report["runs"][0]["environment"]["installed"]["pyyaml"] == "6.0.3"
        assert (
            "ACTUAL_YAML 6.0.3 /workspace/environment/yaml/__init__.py 9"
            in report["runs"][0]["attempts"][-1]["stdout"]
        )
        assert report["runs"][0]["metrics"]["accuracy"] == 1.0
        assert bool(report["preflight_rechecks"]) == (stage == "full")
        if stage == "full":
            checked = report["preflight_rechecks"][0]
            assert "ACTUAL_INPUT_COUNT 8" in checked["run"]["attempts"][-1]["stdout"]
            assert checked["environment_lock_sha256"] == report["environment_lock_sha256"]
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()
