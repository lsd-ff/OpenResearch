from openresearch.config import Settings
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.docker import DockerSandbox
from openresearch.sandbox.http import HttpSandbox


def build_sandbox(settings: Settings) -> Sandbox:
    if settings.sandbox_backend == "http":
        return HttpSandbox(settings)
    return DockerSandbox(settings)
