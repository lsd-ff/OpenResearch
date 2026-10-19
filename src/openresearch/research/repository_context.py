import ast
import json


def python_api(files: dict[str, bytes]) -> list[dict[str, object]]:
    """只解析有界源码，不导入仓库；补齐截断正文之外的真实调用签名。"""
    output: list[dict[str, object]] = []
    remaining = 16000
    paths = [path for path in sorted(files) if path.endswith(".py")][:64]
    for path in paths:
        if len(files[path]) > 65536:
            continue
        try:
            tree = ast.parse(files[path])
        except (SyntaxError, ValueError):
            continue
        signatures = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                signatures.append(f"{node.name}({ast.unparse(node.args)[:400]})")
            elif isinstance(node, ast.ClassDef):
                signatures.extend(
                    f"{node.name}.{method.name}({ast.unparse(method.args)[:400]})"
                    for method in node.body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        item: dict[str, object] = {"path": path, "signatures": signatures[:12]}
        size = len(json.dumps(item, ensure_ascii=False).encode())
        if size <= remaining:
            output.append(item)
            remaining -= size
    return output


def repository_context(files: dict[str, bytes]) -> str:
    """只把有限源码与说明作为模型输入，保留路径与截断标记。"""
    candidates = sorted(
        files,
        key=lambda path: (
            0 if path.lower().startswith("readme") else 1 if path.endswith("pyproject.toml") else 2,
            path,
        ),
    )
    output = []
    size = 0
    for path in candidates:
        if not path.endswith((".py", ".md", ".rst", ".toml")):
            continue
        raw = files[path][:4000]
        if b"\x00" in raw or size + len(raw) > 16000:
            continue
        output.append(
            {
                "path": path,
                "text": raw.decode("utf-8", errors="replace"),
                "excerpt": len(raw) < len(files[path]),
            }
        )
        size += len(raw)
        if len(output) >= 8:
            break
    return json.dumps({"excerpts": output, "python_api": python_api(files)}, ensure_ascii=False)
