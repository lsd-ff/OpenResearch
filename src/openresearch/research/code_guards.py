"""普通代码自动修复的可验证边界；语法检查不在宿主机运行实验。"""

import ast
import hashlib
import io
import tokenize

from openresearch.errors import ContractError


def code_digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def syntax_tree(code: str) -> ast.Module:
    if not code.strip() or len(code.encode()) > 131072:
        raise ContractError("Python code must be nonempty and at most 128 KiB")
    try:
        return ast.parse(code, feature_version=(3, 12))
    except SyntaxError as exc:
        raise ContractError(f"SyntaxError at line {exc.lineno}: {exc.msg}") from exc


def used_imports(tree: ast.AST) -> set[str]:
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(
                {
                    item.asname or item.name.split(".")[0]: item.name.split(".")[0]
                    for item in node.names
                }
            )
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            aliases.update(
                {item.asname or item.name: node.module.split(".")[0] for item in node.names}
            )
    return {
        aliases[node.id]
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in aliases
    }


def protected_checks(tree: ast.AST, required_imports: set[str]) -> list[str]:
    checks: list[str] = []

    def walk(node: ast.AST, context: tuple[str, ...]) -> None:
        roots = set()
        if isinstance(node, ast.Import):
            roots = {item.name.split(".")[0] for item in node.names}
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots = {node.module.split(".")[0]}
        for root in sorted(roots & required_imports):
            checks.append(repr(context) + "import:" + root)
        test_call = isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id.startswith("test_"))
            or ast.unparse(node.func)
            in {
                "pytest.main",
                "unittest.main",
                "sys.exit",
                "os._exit",
                "exit",
                "quit",
                "pytest.skip",
                "pytest.xfail",
                "pytest.importorskip",
                "unittest.skip",
            }
        )
        if isinstance(node, ast.Assert | ast.Raise) or test_call:
            checks.append(repr(context) + ast.dump(node))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            context += (
                "function:" + node.name,
                "decorators:" + repr([ast.dump(item) for item in node.decorator_list]),
            )
        if isinstance(node, ast.ClassDef):
            context += (
                "class:" + node.name,
                "decorators:" + repr([ast.dump(item) for item in node.decorator_list]),
            )
        if isinstance(node, ast.If | ast.While):
            context += ("condition:" + ast.dump(node.test),)
        if isinstance(node, ast.For | ast.AsyncFor):
            context += ("iteration:" + ast.dump(node.iter),)
        if isinstance(node, ast.Try | ast.TryStar):
            context += (
                "handlers:"
                + repr([ast.dump(item.type) if item.type else "*" for item in node.handlers]),
            )
        for field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            branch = (
                (*context, field)
                if isinstance(
                    node, ast.If | ast.While | ast.For | ast.AsyncFor | ast.Try | ast.TryStar
                )
                else context
            )
            for child in children:
                if isinstance(child, ast.AST):
                    walk(child, branch)

    walk(tree, ())
    return checks


def syntax_tokens(code: str) -> list[tuple[int, str]]:
    tokens: list[tokenize.TokenInfo] = []
    try:
        tokens.extend(tokenize.generate_tokens(io.StringIO(code).readline))
    except (tokenize.TokenError, IndentationError):
        raise ContractError(
            "syntax repair cannot safely verify the original token stream"
        ) from None
    ignored = {
        tokenize.ENCODING,
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    # 缺失冒号可修复；变量、运算、字面量、调用顺序和已有括号不能在语法阶段重写。
    return [
        (
            token.type,
            repr(ast.literal_eval(token.string)) if token.type == tokenize.STRING else token.string,
        )
        for token in tokens
        if token.type not in ignored and token.string != ":"
    ]


def validate_code_repair(original: str, candidate: str) -> None:
    tree = syntax_tree(candidate)
    if original == candidate:
        raise ContractError("code repair did not change the failing program")
    try:
        previous = ast.parse(original, feature_version=(3, 12))
    except SyntaxError:
        # 旧程序无法建树时也保留显式断言/raise 文本，不能借修复语法删除验收。
        lines = {line.rstrip() for line in candidate.splitlines()}
        if any(
            line.rstrip() not in lines
            for line in original.splitlines()
            if line.strip().startswith(("assert ", "raise "))
        ):
            raise ContractError("syntax repair must preserve existing checks") from None
        if syntax_tokens(original) != syntax_tokens(candidate):
            raise ContractError(
                "syntax repair must preserve original operations and control flow"
            ) from None
        return
    imports = used_imports(previous)
    if not imports.issubset(used_imports(tree)):
        raise ContractError("code repair must preserve actually used libraries")
    if protected_checks(previous, imports) != protected_checks(tree, imports):
        raise ContractError("code repair must preserve assertions, raises and their control flow")
    tests = {
        ast.dump(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    }
    for before in ast.walk(previous):
        if (
            isinstance(before, ast.FunctionDef | ast.AsyncFunctionDef)
            and before.name.startswith("test_")
            and ast.dump(before) not in tests
        ):
            raise ContractError("code repair cannot rewrite existing test functions")
