import re

from openresearch.config import Settings
from openresearch.models.compute import ReproductionDecision, ReproductionRequest, SandboxResources


def request_from_intent(intent: str) -> ReproductionRequest | None:
    plain = re.sub(r"```.*?```", "", intent, flags=re.S)
    negative = (
        r"(?:不要|不用|无需|禁止|不需要|不执行|不做|不进行|不跑|不计算|"
        r"without\s+|do\s+not\s+|don't\s+|no\s+)\s*"
        r"(?:(?:进行|执行|使用|启用|跑|run|use)\s*)?"
    )
    no_gpu = bool(re.search(negative + r"(?:GPU|CUDA)", plain, re.I))
    # 否定指令和测试名称不能变成昂贵的训练请求；源码字面量也不授予设备权限。
    affirmative = re.sub(
        negative + r"(?:GPU|CUDA|完整复现|全量训练|完整训练|"
        r"full\s+(?:reproduction|training|run|mode)|BLEU|WMT\d*)"
        + r"(?:\s*(?:完整训练|全量训练))?",
        "",
        plain,
        flags=re.I,
    )
    full = bool(
        re.search(
            r"完整复现|全量训练|完整训练|\bfull\s+(?:reproduction|training|run|mode)\b|"
            r"\b(?:mode|reproduction)\s*[:=]\s*full\b|full\s*模式|\bBLEU\b|WMT",
            affirmative,
            re.I,
        )
    )
    smoke = bool(re.search(r"冒烟|\bsmoke\b|仅.{0,6}核心架构", plain, re.I))
    gpu = bool(
        re.search(
            r"(?:用|使用|启用|需要|with|use|using|on).{0,8}(?:GPU|CUDA)|GPU.{0,5}(?:运行|训练)",
            affirmative,
            re.I,
        )
    )
    cpu = no_gpu or bool(re.search(r"(?:仅|只用|use).{0,4}CPU|CPU.only", plain, re.I))
    if not (full or gpu):
        return None
    return ReproductionRequest(
        mode="smoke" if smoke and not full else "full" if full else "auto",
        device="cpu" if cpu else "gpu" if gpu else "auto",
    )


def decide_mode(
    settings: Settings, request: ReproductionRequest, probe: SandboxResources
) -> ReproductionDecision:
    reasons = []
    for name, observed, required in (
        ("cpu_count", probe.cpu_count, settings.reproduction_full_min_cpu),
        ("memory_mb", probe.memory_mb, settings.reproduction_full_min_memory_mb),
        ("workspace_free_mb", probe.workspace_free_mb, settings.reproduction_full_min_disk_mb),
        ("cuda_gpu_count", len(probe.gpus), settings.reproduction_full_min_gpu),
    ):
        if observed < required:
            reasons.append(f"{name}={observed} < required={required}")
    if probe.gpu_error:
        reasons.append(probe.gpu_error)
    full = request.mode == "full" and not reasons
    allowed = (request.mode != "full" or full) and (request.device != "gpu" or bool(probe.gpus))
    return ReproductionDecision(
        requested_mode=request.mode,
        effective_mode="full" if full else "smoke",
        full_eligible=not reasons,
        reasons=reasons or ["full eligible" if full else "full run was not requested"],
        probe=probe,
        execution_allowed=allowed,
        thresholds={
            "cpu_count": settings.reproduction_full_min_cpu,
            "memory_mb": settings.reproduction_full_min_memory_mb,
            "workspace_free_mb": settings.reproduction_full_min_disk_mb,
            "gpu_count": settings.reproduction_full_min_gpu,
        },
    )
