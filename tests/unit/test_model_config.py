import pytest
from pydantic import ValidationError

from openresearch.config import Settings


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/model",
        "provider.example/v1",
        "https://user:password@provider.example/v1",
        "https://provider.example/v1?key=private",
        "https://provider.example/v1#fragment",
    ],
)
def test_invalid_model_base_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_base_url=url)


def test_unknown_model_protocol_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="unsupported")  # type: ignore[arg-type]


def test_response_tool_requires_anthropic_protocol() -> None:
    with pytest.raises(ValidationError, match="anthropic protocol"):
        Settings(_env_file=None, llm_provider="openai", llm_structured_output="tool")
