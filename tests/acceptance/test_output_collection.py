import asyncio
import os

import pytest

from openresearch.models.output_files import OutputFiles
from openresearch.models.runtime import Status
from openresearch.planner.code import build_code_plan
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("OPENRESEARCH_TEST_DOCKER") != "1", reason="Docker opt-in required"
    ),
]


@pytest.mark.parametrize("linked", [False, True])
async def test_real_output_collection_keeps_images_and_rejects_symlinks(
    settings, repository, files, linked
):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
        '<circle cx="20" cy="20" r="10"/></svg>'
    )
    code = (
        "from pathlib import Path\n"
        "root = Path('/workspace/artifacts')\nroot.mkdir(parents=True)\n"
        + (
            "(root / 'leak.txt').symlink_to('/etc/passwd')\n"
            if linked
            else f"(root / 'plot.svg').write_text({svg!r})\n"
        )
        + "print('collected-real-output')\n"
    )
    plan = build_code_plan("output collection", "a", "s", settings, code)
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "a")
    adapter = DockerSandbox(settings)
    worker = Worker(settings, repository, files, adapter)
    try:
        async with asyncio.timeout(90):
            while repository.get_plan(plan.id).status not in {Status.COMPLETED, Status.FAILED}:
                await worker.tick()
                await asyncio.sleep(0.05)
        finished = repository.get_plan(plan.id)
        if linked:
            assert finished.status == Status.FAILED
            assert "execution_files" not in finished.artifacts
            assert "forbidden links" in finished.nodes[-1].error
        else:
            assert finished.status == Status.COMPLETED, finished.nodes[-1].error
            bundle = OutputFiles.model_validate_json(
                files.read(finished.artifacts["execution_files"])
            )
            assert bundle.files[0].path == "plot.svg"
            assert b"circle" in bundle.files[0].content()
            assert finished.nodes[-1].image_base64 == bundle.preview()
            assert "output_files" in finished.nodes[-1].structured_data
        assert await adapter.inventory() == []
    finally:
        await asyncio.gather(
            *(item.future for item in worker.active.values()), return_exceptions=True
        )
        await adapter.shutdown()
