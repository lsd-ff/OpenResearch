import hashlib

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.runtime_profiles import RuntimeProfile


def selected_profile(settings: Settings, name: str | None) -> RuntimeProfile | None:
    if name is None:
        return None
    profile = settings.runtime_profiles.get(name)
    if profile is None:
        raise ContractError("runtime profile is not allowed by the service configuration")
    return profile


def profile_fingerprint(profile: RuntimeProfile) -> str:
    return hashlib.sha256(profile.model_dump_json().encode()).hexdigest()
