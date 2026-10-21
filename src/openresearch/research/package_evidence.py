import asyncio

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from openresearch.sources.packages import PackageSource
from openresearch.sources.stdlib import is_stdlib


async def package_evidence(
    source: PackageSource, requests: list[str], constraints: list[str]
) -> list[dict[str, str]]:
    selected = {}
    candidates = [*constraints, *requests]
    # 当前精确版本优先；不能用无约束的最新发行版解释实际失败。
    candidates.sort(
        key=lambda value: (
            not any(
                pin.operator == "==" and "*" not in pin.version
                for pin in Requirement(value).specifier
            )
        )
    )
    for value in candidates:
        requirement = Requirement(value)
        name = canonicalize_name(requirement.name)
        if is_stdlib(name) or name in selected:
            continue
        pins = list(requirement.specifier)
        selected[name] = pins[0].version if len(pins) == 1 and pins[0].operator == "==" else None
        if len(selected) == 4:
            break
    try:
        async with asyncio.timeout(10):
            descriptions = await asyncio.gather(
                *(source.describe(name, version) for name, version in selected.items()),
                return_exceptions=True,
            )
    except TimeoutError:
        return []
    return [
        {
            key: str(value)
            for key, value in description.model_dump().items()
            if key in {"name", "version", "requires_python", "metadata_url", "metadata_sha256"}
        }
        for description in descriptions
        if not isinstance(description, BaseException)
    ]
