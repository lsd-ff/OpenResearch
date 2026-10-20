from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.claims import ClaimDraft, CriterionDraft, RubricDraft
from openresearch.models.comparison import FrameworkTarget
from openresearch.models.planning import ExperimentDesign
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.research.framework_protocol import read_protocol
from openresearch.sources.frameworks import FrameworkSources
from openresearch.storage.files import ArtifactStore


class FrameworkDesigner:
    def __init__(self, settings: Settings, files: ArtifactStore) -> None:
        self.sources = FrameworkSources(settings)
        self.files = files

    async def execute(self, plan: Plan, task: Task) -> TaskResult:
        if "framework.design" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to prepare a real framework")
        target = FrameworkTarget.model_validate(task.inputs["target"])
        protocol = read_protocol(plan, self.files)
        if protocol.benchmark is None:
            raise ContractError("framework execution requires a shared benchmark protocol")
        outputs = {}
        if "environment_preparation_report" in task.output_artifacts:
            prepared = await self.sources.prepare(
                target,
                plan.user_intent,
                2 if "environment.repair" in task.contract.allowed_tools else 0,
            )
            outputs["environment_preparation_report"] = prepared.report.model_dump_json()
            if (
                not prepared.report.success
                or prepared.environment is None
                or prepared.lock is None
                or prepared.source is None
            ):
                return TaskResult(
                    success=False,
                    error=prepared.report.attempts[-1].error,
                    result="Framework environment failed; actual diagnostics preserved.",
                    outputs=outputs,
                )
            environment, lock, source = prepared.environment, prepared.lock, prepared.source
        else:
            environment, lock, source = await self.sources.lock(target)
        rubric = RubricDraft(
            paper_title="Actual framework execution protocol",
            claims=[
                ClaimDraft(
                    title="Actual library and complete predictions",
                    statement="The installed framework is called and every row is predicted.",
                    criteria=[
                        CriterionDraft(
                            description=description, metric_name=metric, expected_value=1.0
                        )
                        for metric, description in [
                            ("framework_module_usage", "Actual framework functions were called"),
                            ("prediction_coverage", "Every sample has a validated prediction"),
                        ]
                    ],
                )
            ],
        )
        spec = ResearchSpec(
            environment=environment, benchmark=protocol.benchmark, max_repairs=2, rubric=rubric
        )
        design = ExperimentDesign(
            dependencies=[f"{package.name}=={package.version}" for package in source.packages],
            rubric=rubric,
            rationale="Run the actual pinned library on the shared protocol.",
            limitations=[protocol.scope],
        )
        return TaskResult(
            result=design.rationale,
            outputs={
                **outputs,
                "research_spec": spec.model_dump_json(),
                "experiment_design": design.model_dump_json(),
                "environment_lock": lock.model_dump_json(),
                "framework_source": source.model_dump_json(),
            },
        )
