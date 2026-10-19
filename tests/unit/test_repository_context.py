import json

from openresearch.research.repository_context import pytest_paths, repository_context


def test_truncated_excerpts_still_expose_actual_module_apis_without_execution():
    files = {f"a{index}.md": b"intro" for index in range(8)}
    files["z/encoder.py"] = (
        b"raise RuntimeError('source must never be executed')\n"
        b"from numba import jit\n"
        b"class Encoder:\n"
        b"    def __init__(self, vocab, heads=2, *, dropout=0): pass\n"
        b"    def forward(self, tokens, mask, training): pass\n"
    )
    context = json.loads(repository_context(files))
    assert "z/encoder.py" not in [item["path"] for item in context["excerpts"]]
    encoder = next(item for item in context["python_api"] if item["path"] == "z/encoder.py")
    assert "Encoder.__init__(self, vocab, heads=2, *, dropout=0)" in encoder["signatures"]
    assert "Encoder.forward(self, tokens, mask, training)" in encoder["signatures"]
    assert encoder["imports"] == ["from numba import jit"]


def test_api_summary_skips_invalid_or_oversized_source_and_keeps_a_byte_budget():
    files = {
        "invalid.py": b"not valid python : :",
        "oversized.py": b"#" + b"x" * 65536,
        **{f"module{index:02}.py": b"def actual(arg): pass" for index in range(100)},
    }
    context = json.loads(repository_context(files))
    api = context["python_api"]
    assert len(api) <= 64
    assert not any(item["path"] in {"invalid.py", "oversized.py"} for item in api)
    assert sum(len(json.dumps(item, ensure_ascii=False).encode()) for item in api) <= 24000


def test_actual_return_contract_is_preserved_without_including_nested_helper_returns():
    context = json.loads(
        repository_context(
            {
                "model.py": b"""
class Model:
    def forward(self, src, target, training):
        def unrelated():
            return loss
        return logits, attention
"""
            }
        )
    )
    assert context["python_api"][0]["returns"]["Model.forward"] == ["(logits, attention)"]


def test_suite_discovery_requires_collectible_definitions_without_importing_code():
    files = {
        "test_core.py": b"raise RuntimeError('never import')\ndef test_core(): assert True",
        "test_script.py": b"print('not a test suite')",
        "intro.ipynb": b"{}",
    }
    assert pytest_paths(files) == ["test_core.py"]
