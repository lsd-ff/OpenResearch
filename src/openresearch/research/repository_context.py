import json


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
    return json.dumps(output, ensure_ascii=False)
