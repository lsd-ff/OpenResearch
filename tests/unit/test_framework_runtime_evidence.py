import io
import zipfile

import pytest

from openresearch.errors import ContractError
from openresearch.sources.frameworks import wheel_sources


def wheel(content):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, text in content.items():
            archive.writestr(path, text)
    return {"actual.whl": stream.getvalue()}


def test_api_context_requires_the_requested_module_in_actual_wheel_bytes():
    raw = wheel({"real_library/base.py": "def retrieve(query): return query"})
    context = wheel_sources(raw, ["real_library"])
    assert context["real_library/base.py"].startswith("def retrieve")
    with pytest.raises(ContractError, match="do not exist"):
        wheel_sources(raw, ["another_library"])


def test_package_prefix_collision_cannot_stand_in_for_real_module():
    raw = wheel({"real_library_fork/base.py": "print('different library')"})
    with pytest.raises(ContractError, match="do not exist"):
        wheel_sources(raw, ["real_library"])
