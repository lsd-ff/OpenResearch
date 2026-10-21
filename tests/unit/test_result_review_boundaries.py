import copy
import json
import math
from xml.etree import ElementTree

import pytest
from test_result_review_integrity import sealed_fixture

from openresearch.errors import ContractError
from openresearch.models.audit import ResultAudit
from openresearch.models.runtime import new_id
from openresearch.research.review_outputs import plot_results
from openresearch.research.review_results import verify_results
from openresearch.research.review_sources import load_review


@pytest.mark.parametrize("change", ["contract", "attempt", "artifact"])
def test_pinned_review_rejects_source_changes_after_snapshot(settings, repository, files, change):
    material = sealed_fixture(settings, repository, files)
    snapshot = material.snapshot
    with repository.database.transaction() as connection:
        plan = repository.database.load(connection, snapshot.source_plan_id)
        target = next(task for task in plan.nodes if task.id == snapshot.source_task_id)
        if change == "contract":
            target.inputs["research"]["arguments"] = ["changed"]
        elif change == "attempt":
            target.run_count += 1
        else:
            plan.artifacts["benchmark_predictions"] = plan.artifacts[
                "benchmark_predictions"
            ].model_copy(update={"id": new_id()})
        repository.database.save(connection, plan)
    with pytest.raises(ContractError, match="changed"):
        load_review(snapshot, "alice", repository, files)


def test_review_rejects_outputs_combined_from_different_execution_attempts(
    settings, repository, files
):
    material = sealed_fixture(settings, repository, files)
    copied = copy.deepcopy(material.plan.artifacts["benchmark_predictions"])
    copied.execution_id = new_id()
    material.plan.artifacts["benchmark_predictions"] = copied
    audit = verify_results(material, settings, repository, files).audit
    assert not audit.integrity_valid and "mix different" in audit.errors[0]


def test_successful_report_cannot_omit_declared_ablation(settings, repository, files):
    material = sealed_fixture(settings, repository, files)
    material.task.inputs["research"]["ablations"] = [
        {"name": "without_feature", "arguments": ["--without-feature"]}
    ]
    audit = verify_results(material, settings, repository, files).audit
    assert not audit.integrity_valid and "omitted a declared" in audit.errors[0]


def test_plot_escapes_labels_and_keeps_finite_geometry_for_negative_and_huge_values():
    audit = ResultAudit(
        source_plan_id="a" * 32,
        source_task_id="b" * 32,
        source_status="completed",
        metrics={
            '<script>alert("untrusted")</script>': {"r2": -1e308},
            "baseline": {"r2": 1e308},
        },
    )
    svg, specification, image = plot_results(audit, None)
    document = ElementTree.fromstring(svg)
    assert not document.findall(".//{http://www.w3.org/2000/svg}script")
    assert "&lt;script&gt;" in svg and image.startswith("data:image/svg+xml;base64,")
    for node in document.findall(".//{http://www.w3.org/2000/svg}rect"):
        assert all(
            math.isfinite(float(node.attrib[key]))
            for key in ("x", "y", "width", "height")
            if key in node.attrib and "%" not in node.attrib[key]
        )
    assert json.loads(specification)["panels"]["r2"][0][1] == -1e308


def test_plot_rejects_nonfinite_values_instead_of_emitting_invalid_svg():
    audit = ResultAudit(
        source_plan_id="a" * 32,
        source_task_id="b" * 32,
        source_status="completed",
        metrics={"invalid": {"score": float("nan")}},
    )
    with pytest.raises(ContractError, match="finite"):
        plot_results(audit, None)
