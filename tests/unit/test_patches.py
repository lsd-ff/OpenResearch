import hashlib

import pytest

from openresearch.errors import ContractError
from openresearch.models.research import FilePatch, ResearchPatch, ResearchSpec
from openresearch.research.patches import apply_patch, cumulative_patch


def test_invalid_second_patch_does_not_mutate_original_files() -> None:
    original = {"main.py": b"print(1)"}
    spec = ResearchSpec(entrypoint="main.py", patch_paths=["main.py"])
    patch = ResearchPatch(
        files=[
            FilePatch(
                path="main.py",
                base_sha256=hashlib.sha256(original["main.py"]).hexdigest(),
                content="print(2)",
            ),
            FilePatch(path="other.py", content="print(3)"),
        ]
    )
    with pytest.raises(ContractError, match="unauthorized"):
        apply_patch(original, patch, spec)
    assert original == {"main.py": b"print(1)"}


def test_cumulative_repair_can_be_replayed_from_original_snapshot() -> None:
    spec = ResearchSpec(entrypoint="main.py", patch_paths=["main.py"])
    original = {"main.py": b"print(1)"}
    intermediate = {"main.py": b"print(2)"}
    final = {"main.py": b"print(3)"}
    patch = cumulative_patch(original, final, spec)
    assert apply_patch(original, patch, spec) == final
    with pytest.raises(ContractError, match="checksum"):
        apply_patch(intermediate, patch, spec)
