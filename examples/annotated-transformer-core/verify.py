"""显式联网/Docker 验收：固定参考仓库的全部文件和十二项真实核心检查。"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import SecretStr

from openresearch.api.app import create_app
from openresearch.config import Settings
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.research.runtime_selection import bind_runtime
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker
from openresearch.sources.blobs import SourceBlobs
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository

COMMIT = "debc9fd747bb2123160a98046ad1c2d4da44a567"


async def completed(client, identifier):
    async with asyncio.timeout(600):
        while True:
            response = await client.get(f"/api/plans/{identifier}")
            response.raise_for_status()
            plan = response.json()["plan_graph"]
            if plan["status"] in {"completed", "failed", "canceled"}:
                assert plan["status"] == "completed", [
                    (node["type"], node["error"]) for node in plan["nodes"] if node["error"]
                ]
                assert all(node["run_count"] == 1 for node in plan["nodes"])
                return plan
            await asyncio.sleep(0.1)


async def queue(client, identifier):
    assert (await client.post(f"/api/plans/{identifier}/execute")).status_code == 409
    assert (await client.post(f"/api/plans/{identifier}/approve")).status_code == 200
    assert (await client.post(f"/api/plans/{identifier}/execute")).status_code == 202
    return await completed(client, identifier)


async def verify(image: str, directory: Path):
    host = Settings()
    settings = host.model_copy(
        update={
            "data_dir": directory,
            "sandbox_backend": "docker",
            "require_approval": True,
            "runtime_profiles": {
                "reference_cpu": RuntimeProfile(image=image, python="3.12", memory_mb=1024)
            },
            "api_token": SecretStr(uuid4().hex),
            "llm_api_key": SecretStr(""),
            "llm_model": "",
            "source_proxy_url": SecretStr(
                host.source_proxy_url.get_secret_value().replace(
                    "host.docker.internal", "127.0.0.1"
                )
            ),
        }
    )
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    repository = Repository(settings.database_url)
    files = ArtifactStore(directory / "artifacts", settings.max_output_bytes * 3)
    sandbox = DockerSandbox(settings)
    created = []
    original = sandbox.create

    async def recorded(plan_id, execution_id, runtime_profile=None):
        identifier = await original(plan_id, execution_id, runtime_profile)
        created.append(identifier)
        return identifier

    sandbox.create = recorded
    worker = Worker(settings, repository, files, sandbox)
    app = create_app(settings, repository, sandbox)
    code = Path(__file__).with_name("core_checks.py").read_text(encoding="utf-8")
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url="http://reference-core",
                headers={
                    "Authorization": "Bearer " + settings.api_token.get_secret_value(),
                    "X-User-Id": "reference-core",
                    "X-Session-Id": uuid4().hex,
                },
            ) as client,
        ):
            background = asyncio.create_task(worker.run())
            try:
                response = await client.post(
                    "/api/plan",
                    json={
                        "intent": "在固定来源执行十二项 Transformer 核心检查；"
                        "不训练、不替换源码、不修复。",
                        "workflow": "reproduce",
                        "planning_mode": "template",
                        "code": code,
                        "research": {
                            "repository": {
                                "url": "https://github.com/harvardnlp/annotated-transformer",
                                "ref": COMMIT,
                            },
                            "environment": {"requirements": []},
                            "max_repairs": 0,
                            "runtime": bind_runtime(settings, "reference_cpu").model_dump(),
                        },
                    },
                )
                response.raise_for_status()
                plan = await queue(client, response.json()["plan_graph"]["id"])
                saved = repository.get_plan(plan["id"])
                snapshot = json.loads(files.read(saved.artifacts["repository_snapshot"]))
                assert snapshot["commit"] == COMMIT and len(snapshot["files"]) == 18
                assert sum(item["size"] for item in snapshot["files"]) == 17351506
                blobs = SourceBlobs(directory / "sources", settings.source_max_file_bytes)
                for item in snapshot["files"]:
                    raw = blobs.read(item["sha256"], item["size"])
                    assert hashlib.sha256(raw).hexdigest() == item["sha256"]
                report = json.loads(files.read(saved.artifacts["research_report"]))
                assert report["success"] and report["repository_commit"] == COMMIT
                attempt = report["runs"][0]["attempts"][-1]
                actual = json.loads(attempt["stdout"].splitlines()[-1])
                assert actual["tests_run"] == 12 and not any(
                    actual[key] for key in ["errors", "failures", "skipped"]
                )
                assert actual["torch"] == "2.7.1+cpu" and actual["device"] == "cpu"
                patch = json.loads(files.read(saved.artifacts["final_patch"]))
                assert all(item["path"] == "openresearch_run.py" for item in patch["files"])
                before = saved.model_dump_json()
                containers = len(created)
                for _ in range(2):
                    assert (await client.get(f"/api/plans/{plan['id']}")).status_code == 200
                assert repository.get_plan(plan["id"]).model_dump_json() == before
                assert len(created) == containers and not await sandbox.inventory()
                for name, value in [("plan", plan), ("snapshot", snapshot), ("report", report)]:
                    (directory / f"{name}.json").write_text(
                        json.dumps(value, indent=2), encoding="utf-8"
                    )
                summary = {
                    "passed": True,
                    "plan_id": plan["id"],
                    "commit": COMMIT,
                    "files": len(snapshot["files"]),
                    "source_bytes": 17351506,
                    "actual_containers": containers,
                    "checks": actual,
                    "source_file_limit": settings.source_max_file_bytes,
                    "scope": (
                        "Explicit pinned-reference CPU core checks; "
                        "no full tutorial, WMT14 training, or BLEU claim."
                    ),
                }
                (directory / "summary.json").write_text(
                    json.dumps(summary, indent=2), encoding="utf-8"
                )
                print(json.dumps(summary), flush=True)
            finally:
                worker.stop()
                await background
    finally:
        repository.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="immutable ID of the declared CPU runtime")
    parser.add_argument(
        "--output", type=Path, default=Path(".data") / ("reference-core-" + uuid4().hex)
    )
    args = parser.parse_args()
    asyncio.run(verify(args.image, args.output.resolve()))


if __name__ == "__main__":
    main()
