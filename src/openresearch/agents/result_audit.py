import asyncio
import json

from openresearch.agents.claim_assess import ClaimAssessor, verification_markdown
from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.audit import AUDIT_ROLES, ReviewSnapshot
from openresearch.models.graph import ancestor_map
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.research.review_outputs import comparison_markdown, plot_results, review_markdown
from openresearch.research.review_results import empty_audit, verify_results
from openresearch.research.review_sources import capture_review, load_review
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


class ResultAuditor:
    def __init__(self, settings: Settings, repository: Repository, files: ArtifactStore) -> None:
        self.settings, self.repository, self.files = settings, repository, files
        self.claims = ClaimAssessor(settings, files)

    async def execute(self, plan: Plan, task: Task) -> TaskResult:
        if task.inputs.get("native_gap_comparison") is True:
            source = task.inputs.get("source_task_id")
            ancestors = ancestor_map({node.id: node for node in plan.nodes})[task.id]
            if not isinstance(source, str) or source not in ancestors:
                raise ContractError("native comparison requires its frozen execution ancestor")
            snapshot = capture_review(plan, source)
            selected = task.model_copy(
                update={
                    "inputs": {
                        "operation": "paper_compare",
                        "snapshot": snapshot.model_dump(),
                    }
                }
            )
            result = await self.review(plan, selected)
            names = {
                "claim_evidence_graph": "gap_before_claim_evidence_graph",
                "claim_verification_report": "gap_before_claim_verification_report",
            }
            result.outputs = {names.get(key, key): value for key, value in result.outputs.items()}
            result.outputs["gap_source_snapshot"] = snapshot.model_dump_json()
            return result
        return await self.review(plan, task)

    async def review(self, plan: Plan, task: Task) -> TaskResult:
        if "results.audit" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to audit saved results")
        operation = task.inputs.get("operation")
        if operation not in AUDIT_ROLES:
            raise ContractError("unknown result review operation")
        snapshot = ReviewSnapshot.model_validate(task.inputs["snapshot"])
        audit = empty_audit(snapshot)
        try:
            material = await asyncio.to_thread(
                load_review, snapshot, plan.owner_id, self.repository, self.files
            )
        except (ContractError, ValueError, OSError) as exc:
            audit.status, audit.integrity_valid = "failed", False
            audit.errors.append(
                str(exc)[:2000] if not isinstance(exc, OSError) else "source artifact unavailable"
            )
            return TaskResult(
                success=False,
                error=audit.errors[0],
                outputs={"audit_report": audit.model_dump_json()},
            )
        verified = await asyncio.to_thread(
            verify_results, material, self.settings, self.repository, self.files
        )
        audit, report = verified.audit, verified.research
        outputs = {"audit_report": audit.model_dump_json()}
        if not audit.integrity_valid:
            return TaskResult(
                success=False, error="实际结果复核失败：" + "; ".join(audit.errors), outputs=outputs
            )
        text = review_markdown(audit, report)
        image = ""
        if operation in {"paper_compare", "claim_evidence_build"}:
            if report is None or "claims.assess" not in task.contract.allowed_tools:
                raise ContractError(
                    "claim review requires an actual research report and assessment permission"
                )
            # 旧判定图不是新的执行证据；只对冻结规则和实际运行重新评估。
            source = material.plan.model_copy(
                update={
                    "artifacts": {
                        key: value
                        for key, value in (material.plan.artifacts | material.supplements).items()
                        if key not in {"claim_evidence_graph", "claim_verification_report"}
                    }
                }
            )
            graph = await self.claims.assess(source, report, {})
            outputs.update(
                claim_evidence_graph=graph.model_dump_json(),
                claim_verification_report=verification_markdown(graph),
            )
            if operation == "paper_compare":
                text = comparison_markdown(audit, report, graph)
                outputs["comparison_report"] = text
            else:
                text += "\n\n" + verification_markdown(graph)
        elif operation in {"render_plot", "result_visualization"}:
            svg, specification, image = await asyncio.to_thread(plot_results, audit, report)
            if not svg:
                return TaskResult(
                    success=False, error="没有可绘制的真实指标或执行统计。", outputs=outputs
                )
            outputs.update(result_plot=svg, plot_spec=specification)
        elif operation == "benchmark_validate":
            if audit.kind != "research" or audit.sample_count is None:
                return TaskResult(
                    success=False, error="没有已声明并实际运行的 Benchmark。", outputs=outputs
                )
            outputs.update(
                benchmark_validation_report=audit.model_dump_json(),
                benchmark_metrics=json.dumps(audit.metrics, allow_nan=False),
            )
        else:
            outputs["verification_report"] = text
        strict = operation in {"verify_result", "benchmark_validate"}
        success = not strict or audit.status == "verified"
        return TaskResult(
            success=success,
            error="源执行失败或证据不足，不能认定验证通过。" if not success else "",
            result=text,
            structured_data=audit.model_dump_json(),
            image_base64=image,
            outputs=outputs,
        )
