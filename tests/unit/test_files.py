import pytest

from openresearch.errors import ContractError
from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import new_id
from openresearch.storage.files import ArtifactStore


def test_modified_evidence_is_rejected(files: ArtifactStore) -> None:
    lease = ExecutionLease(new_id(), new_id(), new_id(), "worker")
    artifact = files.write(lease, "generated_code", "print(1)")
    assert files.read(artifact) == "print(1)"
    (files.root / artifact.location).write_text("print(2)", encoding="utf-8")
    with pytest.raises(ContractError, match="hash mismatch"):
        files.read(artifact)


def test_path_traversal_is_rejected(files: ArtifactStore) -> None:
    lease = ExecutionLease(new_id(), new_id(), new_id(), "worker")
    artifact = files.write(lease, "generated_code", "print(1)")
    artifact.location = "../outside.txt"
    with pytest.raises(ContractError, match="escapes"):
        files.read(artifact)
