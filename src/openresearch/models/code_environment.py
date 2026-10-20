from dataclasses import dataclass

from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.models.sources import EnvironmentLock


@dataclass(frozen=True)
class CodeEnvironment:
    lock: EnvironmentLock
    requests: tuple[str, ...]
    runtime: RuntimeBinding | None = None
    constraints: tuple[str, ...] = ()
