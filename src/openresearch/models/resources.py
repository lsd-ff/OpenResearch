from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedSandbox:
    id: str
    execution_id: str
    created_at: float
