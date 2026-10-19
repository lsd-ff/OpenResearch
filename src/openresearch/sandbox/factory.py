from openresearch.config import Settings
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.docker import DockerSandbox
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.http import HttpSandbox
from openresearch.sandbox.opensandbox import OpenSandbox


def build_sandbox(settings: Settings) -> Sandbox:
    if settings.sandbox_backend == "http":
        return HttpSandbox(settings)
    if settings.sandbox_backend == "opensandbox":
        return OpenSandbox(settings)
    if settings.sandbox_backend == "auto":
        fallback = OpenSandbox(settings) if settings.sandbox_fallback_enabled else None
        return FallbackSandbox(DockerSandbox(settings), fallback)
    return DockerSandbox(settings)
