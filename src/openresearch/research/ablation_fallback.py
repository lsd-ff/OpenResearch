import ast
import re

from openresearch.models.ablation import AblationCandidate, AblationCandidates, AblationCategory
from openresearch.models.research import ExperimentVariant


def category_for(value: str) -> AblationCategory:
    if "seed" in value:
        return "seed_stability"
    if any(word in value for word in ("sample", "data", "size")):
        return "data_scale"
    if any(word in value for word in ("disable", "without", "ablat")):
        return "module"
    return "parameter"


def cli_variants(code: str, arguments: list[str]) -> list[ExperimentVariant]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    variants = []
    for node in ast.walk(tree):
        option: str | None = None
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and any(isinstance(op, ast.In) for op in node.ops)
            and any(ast.unparse(value) == "sys.argv" for value in node.comparators)
        ):
            option = node.left.value
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
        ):
            flag = node.args[0]
            if isinstance(flag, ast.Constant) and isinstance(flag.value, str):
                options = {item.arg: item.value for item in node.keywords}
                action = options.get("action")
                if isinstance(action, ast.Constant) and action.value == "store_true":
                    option = flag.value
        if option is None or option in arguments:
            continue
        name = re.sub(r"[^a-z0-9_-]", "_", option.lower()).strip("-_")[:31]
        if name and name[0].isalpha() and name != "baseline":
            variants.append(ExperimentVariant(name=name, arguments=[*arguments, option]))
    return variants[:8]


def fallback_candidates(
    code: str, variants: list[ExperimentVariant], arguments: list[str]
) -> AblationCandidates | None:
    choices = variants or cli_variants(code, arguments)
    candidates = {}
    for variant in choices:
        candidates[variant.name] = AblationCandidate(
            id=variant.name,
            category=category_for(" ".join(variant.arguments)),
            title=variant.name,
            hypothesis="Compare this declared source/adapter option with baseline",
            change="Use the existing declared arguments: " + repr(variant.arguments),
            arguments=variant.arguments,
            estimated_minutes=1,
        )
    return AblationCandidates(candidates=list(candidates.values())[:8]) if candidates else None
