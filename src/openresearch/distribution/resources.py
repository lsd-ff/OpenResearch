import sys
from pathlib import Path

from openresearch.config import Settings
from openresearch.errors import ContractError

BUNDLE = Path(__file__).resolve().parents[1] / "_bundle"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_uv() -> str | None:
    if not frozen():
        return None
    executable = BUNDLE / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    if not executable.is_file():
        raise ContractError("release is missing its bundled uv executable")
    return str(executable)


def web_root(settings: Settings) -> Path:
    candidates = [settings.web_dir] if settings.web_dir is not None else [BUNDLE / "web"]
    if settings.web_dir is None and not frozen():
        candidates.append(Path(__file__).resolve().parents[3] / "frontend" / "dist")
    for candidate in candidates:
        if candidate is not None and (candidate / "index.html").is_file():
            return candidate.resolve()
    raise ContractError("workbench index.html is missing; build the frontend or set WEB_DIR")


def load_settings(env_file: Path | None = None) -> Settings:
    if env_file is None and not frozen():
        return Settings()
    executable_dir = Path(sys.executable).resolve().parent
    settings = Settings(_env_file=env_file or executable_dir / ".env")
    if frozen() and "data_dir" not in settings.model_fields_set:
        settings = settings.model_copy(update={"data_dir": executable_dir / ".data"})
    return settings
