import ast
import hashlib
import re

from openresearch.errors import ContractError
from openresearch.models.research import FilePatch, ResearchPatch, ResearchSpec
from openresearch.models.sources import RepositorySnapshot
from openresearch.sources.blobs import SourceBlobs


def load_sources(snapshot: RepositorySnapshot | None, blobs: SourceBlobs) -> dict[str, bytes]:
    if snapshot is None:
        return {}
    return {source.path: blobs.read(source.sha256, source.size) for source in snapshot.files}


def apply_patch(
    files: dict[str, bytes], patch: ResearchPatch, spec: ResearchSpec
) -> dict[str, bytes]:
    changed: dict[str, bytes] = {}
    if sum(len(item.content.encode()) for item in patch.files) > 131072:
        raise ContractError("combined patch exceeds 128 KiB")
    for item in patch.files:
        if item.path not in spec.patch_paths or item.path in changed:
            raise ContractError("patch modifies an unauthorized or duplicate path")
        base = hashlib.sha256(files[item.path]).hexdigest() if item.path in files else None
        if item.base_sha256 != base:
            raise ContractError("patch base checksum does not match the current workspace")
        try:
            ast.parse(item.content)
        except SyntaxError as exc:
            raise ContractError(
                f"patch contains invalid Python syntax at line {exc.lineno}"
            ) from exc
        changed[item.path] = item.content.encode()
    # 先验证完整补丁再发布新映射，失败时原始源码不发生局部改动。
    merged = files | changed
    if spec.entrypoint not in merged:
        raise ContractError("research entrypoint does not exist after applying the patch")
    if spec.test_paths:
        if any(path not in files for path in spec.test_paths):
            raise ContractError("selected pytest source does not exist in the pinned repository")
        if merged[spec.entrypoint] != pytest_launcher(spec.test_paths).encode():
            raise ContractError("the selected pytest launcher must not be replaced or filtered")
    return merged


def pytest_launcher(paths: list[str]) -> str:
    # 只生成测试启动器；测试内容来自固定仓库，退出码原样传给真实沙箱执行结果。
    arguments = ["-v", "-s", "--tb=short", "--", *paths]
    return "import pytest\n\nraise SystemExit(pytest.main(" + repr(arguments) + "))\n"


def cumulative_patch(
    original: dict[str, bytes], current: dict[str, bytes], spec: ResearchSpec
) -> ResearchPatch:
    """把连续修复归并到原始快照上，最终补丁可从固定提交独立回放。"""
    patch = ResearchPatch(
        files=[
            FilePatch(
                path=path,
                base_sha256=hashlib.sha256(original[path]).hexdigest()
                if path in original
                else None,
                content=current[path].decode("utf-8"),
            )
            for path in spec.patch_paths
            if path in current and current[path] != original.get(path)
        ]
    )
    apply_patch(original, patch, spec)
    return patch


def validate_generated_tests(intent: str, files: dict[str, bytes], spec: ResearchSpec) -> None:
    """请求测试时，拒绝只打印成功、常量断言或占位代码冒充测试程序。"""
    if spec.benchmark or not re.search(r"测试|\btests?\b", intent, re.I):
        return
    tree = ast.parse(files[spec.entrypoint])
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and not isinstance(node.test, ast.Constant):
            return
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr.startswith("assert") or (
            node.func.attr == "main"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"pytest", "unittest"}
        ):
            return
    raise ContractError(
        "generated test entrypoint has no executable nonconstant assertions or test runner"
    )
