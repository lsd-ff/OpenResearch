import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openresearch.checkpoint.registry import Snapshot, SnapshotRegistry
from openresearch.errors import SandboxError
from openresearch.sandbox.core.models import CoreConfig
from openresearch.sandbox.core.output import OutputCapture
from openresearch.sandbox.core.session import CoreSandbox


def test_core_config_keeps_mutable_storage_separate_and_rejects_unsafe_options(tmp_path: Path):
    config = CoreConfig(data_dir=tmp_path, environment={"VALUE": "a'b\n中文"})
    assert config.network == "none"
    assert config.runtime == "runc"
    assert "a'b" not in repr(config)
    for override in (
        {"environment": {"bad-name": "x"}},
        {"environment": {"VALUE": "x\x00y"}},
        {"network": "host"},
        {"volumes": {"/": "/host"}},
        {"timeout_seconds": 0},
        {"working_dir": "/workspace/../host"},
    ):
        with pytest.raises(ValidationError):
            CoreConfig(data_dir=tmp_path, **override)


def test_registry_replaces_node_in_order_and_prunes_only_after_real_restore():
    registry = SnapshotRegistry()
    first = Snapshot("a", "sha256:first", 1)
    latest = Snapshot("b", "sha256:latest", 2)
    replacement = Snapshot("a", "sha256:replacement", 3)
    registry.register(first)
    registry.register(latest)
    registry.register(replacement)
    assert registry.all() == (replacement, latest)
    assert registry.latest() == latest
    with pytest.raises(KeyError):
        registry.restore("missing")
    assert registry.all() == (replacement, latest)
    registry.restore("a")
    assert registry.all() == (replacement,)
    assert registry.latest() == replacement
    with pytest.raises(KeyError):
        registry.get("b")


def test_output_budget_and_summary_preserve_actual_bytes_and_unicode(tmp_path: Path):
    config = CoreConfig(
        data_dir=tmp_path, max_output_bytes=1024, keep_first_lines=0, keep_last_lines=1
    )
    raw = "开始\n中间\n最后\n".encode()
    with OutputCapture(tmp_path, "real command", config) as capture:
        capture.write(raw)
        result = capture.finish(0, False)
    assert result.succeeded and result.audit_path.read_bytes() == raw
    assert "最后" in result.summary and "开始" not in result.summary
    receipt = json.loads(result.audit_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert receipt["captured_sha256"] == hashlib.sha256(raw).hexdigest()
    with OutputCapture(tmp_path, "too much", config) as capture:
        capture.write(b"x" * 1024)
        capture.write(b"y")
        limited = capture.finish(0, False)
    assert not limited.succeeded and limited.output_limited
    assert limited.audit_path.read_bytes() == b"x" * 1024


def test_disconnected_command_keeps_partial_audit(tmp_path: Path):
    capture = OutputCapture(tmp_path, "broken", CoreConfig(data_dir=tmp_path))
    with pytest.raises(SandboxError), capture:
        capture.write(b"partial")
        raise SandboxError("disconnected")
    assert capture.path.read_bytes() == b"partial"
    receipt = json.loads(capture.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert receipt["error_type"] == "SandboxError" and receipt["partial"]


async def test_unstarted_close_is_idempotent_and_cannot_be_reopened(tmp_path: Path):
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path))
    await sandbox.close()
    await sandbox.close()
    with pytest.raises(SandboxError, match="cannot be started twice"):
        await sandbox.__aenter__()


def test_deep_audit_paths_preserve_actual_output_and_metadata(tmp_path: Path):
    root = tmp_path / ("first" * 12) / ("second" * 10) / ("third" * 12)
    with OutputCapture(root, "deep audit", CoreConfig(data_dir=root)) as capture:
        capture.write(b"actual bytes")
        result = capture.finish(0, False)
    assert result.audit_path.read_bytes() == b"actual bytes"
    assert len(str(result.audit_path)) > 260
    metadata = json.loads(result.audit_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["captured_bytes"] == 12 and metadata["exit_code"] == 0
