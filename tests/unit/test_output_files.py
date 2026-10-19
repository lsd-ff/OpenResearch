import base64
import hashlib

import pytest

from openresearch.models.output_files import OutputFile, OutputFiles


def output(path="plot.svg", raw=b'<svg xmlns="http://www.w3.org/2000/svg"/>'):
    return {
        "path": path,
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "base64": base64.b64encode(raw).decode(),
    }


def test_output_collection_has_verified_download_bytes_and_typed_preview():
    bundle = OutputFiles(files=[OutputFile(**output())])
    assert bundle.files[0].content().startswith(b"<svg")
    assert bundle.preview().startswith("data:image/svg+xml;base64,")


@pytest.mark.parametrize("fault", ["path", "checksum", "size", "encoding", "duplicate"])
def test_file_outputs_reject_path_escape_or_corrupted_bytes(fault):
    value = output()
    if fault == "path":
        value["path"] = "../outside.svg"
    elif fault == "checksum":
        value["sha256"] = "0" * 64
    elif fault == "size":
        value["size"] += 1
    elif fault == "encoding":
        value["base64"] = "not base64!"
    with pytest.raises(ValueError):
        OutputFiles.model_validate({"files": [value, value] if fault == "duplicate" else [value]})
