"""One configuration source shared by the API and worker."""

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from openresearch.models.runtime_profiles import RuntimeProfile


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENRESEARCH_", env_file=".env", extra="ignore")

    data_dir: Path = Path(".data")
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    web_dir: Path | None = None
    sandbox_backend: Literal["docker", "http", "opensandbox", "auto"] = "docker"
    sandbox_url: str = "http://127.0.0.1:8082"
    sandbox_token: SecretStr = SecretStr("")
    sandbox_service_port: int = Field(default=8082, ge=1, le=65535)
    sandbox_fallback_enabled: bool = False
    opensandbox_url: str = "http://127.0.0.1:8088"
    opensandbox_api_key: SecretStr = SecretStr("")
    sandbox_image: str = "python:3.12-slim"
    runtime_profiles: dict[str, RuntimeProfile] = Field(default_factory=dict, max_length=16)
    sandbox_network: Literal["none", "bridge"] = "none"
    sandbox_memory: str = "1g"
    sandbox_tmpfs_size_mb: int = Field(default=256, ge=16, le=2048)
    sandbox_workspace_size_mb: int = Field(default=1024, ge=64, le=4096)
    api_token: SecretStr = SecretStr("")
    require_approval: bool = False
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    max_concurrent_tasks: int = Field(default=2, ge=1, le=16)
    task_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    plan_timeout_seconds: int = Field(default=2400, ge=1, le=86400)
    max_task_attempts: int = Field(default=100, ge=1, le=1000)
    code_max_repairs: int = Field(default=2, ge=0, le=2)
    lease_seconds: int = Field(default=30, ge=3, le=300)
    poll_interval_seconds: float = Field(default=0.25, gt=0, le=10)
    max_output_bytes: int = Field(default=1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    upload_max_bytes: int = Field(default=32 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    document_max_pages: int = Field(default=100, ge=1, le=300)
    document_max_text_bytes: int = Field(default=512000, ge=1024, le=2 * 1024 * 1024)
    document_timeout_seconds: int = Field(default=60, ge=1, le=300)
    source_max_archive_bytes: int = Field(default=16 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    source_max_bytes: int = Field(default=32 * 1024 * 1024, ge=1024, le=128 * 1024 * 1024)
    source_max_file_bytes: int = Field(default=4 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)
    source_max_files: int = Field(default=2000, ge=1, le=10000)
    source_proxy_url: SecretStr = SecretStr("")
    source_download_timeout_seconds: int = Field(default=60, ge=10, le=600)
    dependency_max_bytes: int = Field(default=128 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    benchmark_max_rows: int = Field(default=2000, ge=1, le=100000)
    planner_mode: Literal["llm", "template"] = "llm"
    intent_service_url: str = ""
    intent_service_port: int = Field(default=8083, ge=1, le=65535)
    intent_service_token: SecretStr = SecretStr("")
    llm_provider: Literal["openai", "anthropic"] = "openai"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: int = Field(default=60, ge=1, le=300)
    llm_max_output_tokens: int = Field(default=8192, ge=128, le=65536)
    llm_schema_retries: int = Field(default=2, ge=0, le=2)
    llm_thinking: Literal["default", "disabled"] = "default"
    llm_structured_output: Literal["prompt", "tool"] = "prompt"
    llm_max_input_bytes: int = Field(default=131072, ge=4096, le=1048576)
    llm_max_response_bytes: int = Field(default=512000, ge=4096, le=2 * 1024 * 1024)

    @field_validator("data_dir")
    @classmethod
    def absolute_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("runtime_profiles")
    @classmethod
    def profile_names(cls, values: dict[str, RuntimeProfile]) -> dict[str, RuntimeProfile]:
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name) for name in values):
            raise ValueError(
                "runtime profile names must use lowercase letters, numbers, underscores or hyphens"
            )
        return values

    @field_validator("runtime_profiles", mode="before")
    @classmethod
    def empty_profiles(cls, value: object) -> object:
        return {} if value is None else value

    @field_validator("llm_base_url", "sandbox_url", "opensandbox_url")
    @classmethod
    def validate_llm_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "model base URL must be HTTP(S) without credentials, query or fragment"
            )
        return value.rstrip("/")

    @field_validator("intent_service_url")
    @classmethod
    def validate_intent_url(cls, value: str) -> str:
        return cls.validate_llm_url(value) if value else ""

    @field_validator("source_proxy_url")
    @classmethod
    def validate_source_proxy(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value():
            parts = urlsplit(value.get_secret_value())
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.query
                or parts.fragment
            ):
                raise ValueError("source proxy must be an HTTP(S) proxy URL")
        return value

    @model_validator(mode="after")
    def validate_lease_polling(self) -> "Settings":
        if self.poll_interval_seconds >= self.lease_seconds / 3:
            raise ValueError("poll interval must be less than one third of the lease duration")
        if self.llm_structured_output == "tool" and self.llm_provider != "anthropic":
            raise ValueError("structured response tools require the anthropic protocol")
        return self

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'runtime.sqlite3').as_posix()}"
