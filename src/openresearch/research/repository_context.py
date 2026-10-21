import ast
import json

MAX_API_SOURCE_BYTES = 128 * 1024


def pytest_paths(files: dict[str, bytes]) -> list[str]:
    """按真实文件名与定义找测试候选，只解析 AST，不在宿主机导入。"""
    paths = []
    for path, raw in files.items():
        name = path.rsplit("/", 1)[-1]
        if (
            not path.endswith(".py")
            or not (name.startswith("test_") or name.endswith("_test.py"))
            or len(raw) > MAX_API_SOURCE_BYTES
        ):
            continue
        try:
            nodes = ast.parse(raw).body
        except (SyntaxError, ValueError):
            continue
        if any(
            (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
            )
            or (
                isinstance(node, ast.ClassDef)
                and node.name.startswith("Test")
                and any(
                    isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and method.name.startswith("test_")
                    for method in node.body
                )
            )
            for node in nodes
        ):
            paths.append(path)
    return paths[:8]


def python_api(files: dict[str, bytes]) -> list[dict[str, object]]:
    """只解析有界源码，不导入仓库；补齐截断正文之外的真实调用签名。"""
    output: list[dict[str, object]] = []
    remaining = 24000
    paths = [path for path in sorted(files) if path.endswith(".py")][:64]
    for path in paths:
        if len(files[path]) > MAX_API_SOURCE_BYTES:
            continue
        try:
            tree = ast.parse(files[path])
        except (SyntaxError, ValueError):
            continue
        signatures = []
        returns: dict[str, list[str]] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                signatures.append(f"{node.name}({ast.unparse(node.args)[:400]})")
                returns[node.name] = return_expressions(node)
            elif isinstance(node, ast.ClassDef):
                signatures.extend(
                    f"{node.name}.{method.name}({ast.unparse(method.args)[:400]})"
                    for method in node.body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
                returns.update(
                    {
                        f"{node.name}.{method.name}": return_expressions(method)
                        for method in node.body
                        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not method.name.startswith("_")
                    }
                )
        item: dict[str, object] = {
            "path": path,
            "signatures": signatures[:64],
            "returns": dict(list(returns.items())[:64]),
            "imports": [
                ast.unparse(node)
                for node in tree.body
                if isinstance(node, (ast.Import, ast.ImportFrom))
            ][:12],
        }
        size = len(json.dumps(item, ensure_ascii=False).encode())
        if size <= remaining:
            output.append(item)
            remaining -= size
    return output


def return_expressions(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    # 返回表达式来自实际 AST；避免把 (logits, attention) 猜成 (loss, logits)。
    expressions: list[str] = []
    pending = list(ast.iter_child_nodes(node))
    while pending:
        item = pending.pop()
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(item, ast.Return) and item.value is not None:
            expressions.append(ast.unparse(item.value)[:160])
        pending.extend(ast.iter_child_nodes(item))
    return list(dict.fromkeys(expressions))[:4]


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
