"""旧字段投影只读取受管阶段的实际产物，路径返回配置引用。"""

import json
from contextlib import suppress

from openresearch.agents.scopes import scoped_plan_task
from openresearch.api.dependencies import Runtime
from openresearch.models.claims import FrozenRubric
from openresearch.models.research import ResearchPatch
from openresearch.models.runtime import Plan, Task
from openresearch.models.sources import RepositorySnapshot
from openresearch.research.patches import apply_patch, load_sources
from openresearch.research.specifications import resolved_research
from openresearch.sources.blobs import SourceBlobs


def stage_outputs(runtime: Runtime, plan: Plan, task: Task) -> dict[str, str]:
    projected, target = scoped_plan_task(plan, task)
    if target.type == "claim_freeze" and "claim_rubric" in projected.artifacts:
        rubric = FrozenRubric.model_validate_json(
            runtime.files.read(projected.artifacts["claim_rubric"])
        )
        return {
            "claim_rubric_report": json.dumps(
                {
                    "source_artifact": rubric.source_artifact,
                    "sha256": rubric.sha256,
                    "claim_count": len(rubric.claims),
                    "status": "frozen_before_execution",
                }
            )
        }
    if target.type != "research_prepare":
        return {}
    spec = resolved_research(projected, target, runtime.files)
    snapshot = (
        RepositorySnapshot.model_validate_json(runtime.files.read(artifact))
        if (artifact := projected.artifacts.get("repository_snapshot"))
        else None
    )
    sources = load_sources(
        snapshot,
        SourceBlobs(runtime.settings.data_dir / "sources", runtime.settings.source_max_file_bytes),
    )
    patch = ResearchPatch.model_validate_json(
        runtime.files.read(projected.artifacts["research_patch"])
    )
    prepared = apply_patch(sources, patch, spec)
    outputs = {
        "workspace_ref": "openresearch:" + plan.id,
        "workspace_path": "openresearch:" + plan.id,
        "code_file_path": spec.entrypoint,
    }
    if snapshot:
        outputs["repo_manifest"] = snapshot.model_dump_json()
    if spec.entrypoint in prepared:
        # 原始非 UTF-8 文件仍由快照摘要标识，不能伪造一个经过改写的源码字符串。
        with suppress(UnicodeDecodeError):
            outputs["generated_code"] = prepared[spec.entrypoint].decode("utf-8")
    return outputs
