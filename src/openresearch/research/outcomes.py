import json

from openresearch.models.research import ResearchPatch, ResearchReport, ResearchRun
from openresearch.models.runtime import TaskResult


def research_result(
    report: ResearchReport, patch: ResearchPatch, predictions: dict[str, list[dict[str, object]]]
) -> TaskResult:
    error = next((run.attempts[-1].error for run in report.runs if run.attempts[-1].error), "")
    return TaskResult(
        success=report.success,
        error=error,
        result="Experiment completed."
        if report.success
        else "Experiment failed; evidence preserved.",
        outputs={
            "research_report": report.model_dump_json(),
            "benchmark_predictions": json.dumps(predictions, ensure_ascii=False),
            "final_patch": patch.model_dump_json(),
        },
    )


def comparison(runs: list[ResearchRun]) -> dict[str, dict[str, float]]:
    baseline = runs[0].metrics
    return {
        run.variant: {key: value - baseline[key] for key, value in run.metrics.items()}
        for run in runs[1:]
    }
