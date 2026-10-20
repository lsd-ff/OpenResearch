from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.compute import ReproductionRequest
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.research.runtime_selection import (
    bind_runtime,
    bound_profile,
    intent_python,
    runtime_for_intent,
)


def compute_runtime(
    settings: Settings,
    intent: str,
    request: ReproductionRequest | None,
    binding: RuntimeBinding | None = None,
) -> RuntimeBinding | None:
    if request is None:
        return runtime_for_intent(settings, intent, binding)
    if binding:
        profile = bound_profile(settings, binding)
        if profile and request.device != "auto" and profile.gpu != (request.device == "gpu"):
            raise ContractError("registered runtime conflicts with the requested compute device")
        return runtime_for_intent(settings, intent, binding, allow_gpu=True)
    requested = intent_python(intent)
    want_gpu = request.device == "gpu" or (request.mode == "full" and request.device == "auto")
    candidates = [
        (name, profile)
        for name, profile in sorted(settings.runtime_profiles.items())
        if profile.gpu == want_gpu and (requested is None or profile.python + ".0" in requested)
    ]
    if request.mode == "full":
        # 优先选择管理员已明确配置足额的环境；真正准入仍以容器内探测为准。
        candidates.sort(
            key=lambda item: (
                not (
                    item[1].cpus >= settings.reproduction_full_min_cpu
                    and (item[1].memory_mb or 0) >= settings.reproduction_full_min_memory_mb
                    and (
                        item[1].workspace_storage == "volume"
                        or settings.sandbox_workspace_size_mb
                        >= settings.reproduction_full_min_disk_mb
                    )
                )
            )
        )
    if candidates:
        return bind_runtime(settings, candidates[0][0])
    if request.device == "gpu":
        raise ContractError("no registered GPU runtime satisfies the requested Python version")
    return runtime_for_intent(settings, intent)
