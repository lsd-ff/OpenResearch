"""从所属持久图捕获、复核审核输入；不接收客户端产物正文或路径。"""

import hashlib
import json
from dataclasses import dataclass, field

from openresearch.agents.scopes import scoped_plan_task
from openresearch.errors import ConflictError, ContractError
from openresearch.models.audit import ArtifactPin, ReviewSnapshot
from openresearch.models.graph import ancestor_map
from openresearch.models.runtime import Artifact, Plan, Status, Task
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository

TERMINAL = {Status.COMPLETED, Status.FAILED, Status.BLOCKED, Status.CANCELED}
SOURCE_TYPES = {"research_execute", "research_gap_repair", "code_execute", "data_analyze"}
SUPPLEMENT_KEYS = {
    "audit_report",
    "comparison_report",
    "claim_evidence_graph",
    "claim_verification_report",
    "result_plot",
    "plot_spec",
}


@dataclass(frozen=True)
class ReviewMaterial:
    plan: Plan
    task: Task
    snapshot: ReviewSnapshot
    supplements: dict[str, Artifact] = field(default_factory=dict)


def pin(plan_id: str, artifact: Artifact) -> ArtifactPin:
    return ArtifactPin(plan_id=plan_id, artifact_id=artifact.id, sha256=artifact.sha256)


def contract_digest(plan: Plan, task: Task) -> str:
    value = {
        "intent": plan.user_intent,
        "intent_type": plan.intent_type,
        "attachments": plan.attachments,
        "task": task.model_dump(
            include={
                "id",
                "type",
                "inputs",
                "contract",
                "dependencies",
                "required_artifacts",
                "output_artifacts",
                "assigned_to",
                "artifact_scope",
            }
        ),
    }
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def source_view(plan: Plan, task: Task) -> Plan:
    ancestors = ancestor_map({node.id: node for node in plan.nodes})[task.id] | {task.id}
    projected, _ = scoped_plan_task(plan, task)
    return projected.model_copy(
        update={
            "artifacts": {
                key: artifact
                for key, artifact in projected.artifacts.items()
                if artifact.producer_task_id in ancestors
                and (task.artifact_scope != "result_gap" or artifact.producer_task_id == task.id)
            }
        }
    )


def capture_review(plan: Plan, task_id: str | None = None) -> ReviewSnapshot:
    candidates = [
        task
        for task in plan.nodes
        if task.type in SOURCE_TYPES and (task_id is None or task.id == task_id)
    ]
    if len(candidates) != 1:
        raise ContractError("review source is absent or ambiguous; select an actual stage_task_id")
    task = candidates[0]
    if task.status not in TERMINAL:
        raise ConflictError("review requires a terminal execution or data stage")
    view = source_view(plan, task)
    return ReviewSnapshot(
        source_plan_id=plan.id,
        source_task_id=task.id,
        source_run_count=task.run_count,
        source_contract_sha256=contract_digest(plan, task),
        source_status=task.status,
        artifacts={key: pin(plan.id, artifact) for key, artifact in view.artifacts.items()},
    )


def load_review(
    snapshot: ReviewSnapshot, owner: str, repository: Repository, files: ArtifactStore
) -> ReviewMaterial:
    source = repository.get_plan(snapshot.source_plan_id, owner)
    task = next((node for node in source.nodes if node.id == snapshot.source_task_id), None)
    if (
        task is None
        or task.status != snapshot.source_status
        or task.run_count != snapshot.source_run_count
    ):
        raise ContractError("review source changed after its snapshot; request a new review")
    if contract_digest(source, task) != snapshot.source_contract_sha256:
        raise ContractError("review source contract changed after its snapshot")
    view = source_view(source, task)
    if set(view.artifacts) != set(snapshot.artifacts):
        raise ContractError("review source artifact inventory changed")
    for key, reference in snapshot.artifacts.items():
        if pin(source.id, view.artifacts[key]) != reference:
            raise ContractError("review source artifact identity changed")
    supplements: dict[str, Artifact] = {}
    for key, reference in snapshot.supplements.items():
        if key not in SUPPLEMENT_KEYS:
            raise ContractError("unsupported review supplement")
        artifact = repository.get_artifact(reference.plan_id, reference.artifact_id, owner)
        if artifact.sha256 != reference.sha256 or artifact.key != key:
            raise ContractError("review supplement identity changed")
        supplements[key] = artifact
    # 即使是服务端生成的引用，也重新核对实际文件摘要；不信任预览或内存中的正文。
    for artifact in [*view.artifacts.values(), *supplements.values()]:
        files.read(artifact)
    _, projected_task = scoped_plan_task(source, task)
    return ReviewMaterial(view, projected_task, snapshot, supplements)
