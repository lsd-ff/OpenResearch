"""纯元数据校验；同一算法也会随可信安装器进入沙箱，绝不导入实验依赖。"""

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def validate_dependency_graph(
    requirements: dict[str, list[str]], pins: dict[str, str], environment: dict[str, str]
) -> None:
    extras = {name: {""} for name in requirements}
    # 循环依赖可能逐层激活 extra；只传播元数据中的有限集合，直至不再新增。
    changed = True
    while changed:
        changed = False
        for package, values in requirements.items():
            for value in values:
                dependency = Requirement(value)
                if dependency.marker and not any(
                    dependency.marker.evaluate({**environment, "extra": extra})
                    for extra in extras[package]
                ):
                    continue
                name = canonicalize_name(dependency.name)
                if dependency.url or name not in pins or pins[name] not in dependency.specifier:
                    raise ValueError(
                        f"dependency lock must include compatible exact pin: {dependency}"
                    )
                if name not in extras:
                    raise ValueError("dependency metadata is missing from the frozen graph")
                activated = {canonicalize_name(extra) for extra in dependency.extras}
                if activated - extras[name]:
                    extras[name].update(activated)
                    changed = True
