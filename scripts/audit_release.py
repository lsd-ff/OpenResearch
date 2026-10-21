"""检查实际归档资源及配置密钥；只输出数量和通过状态，不打印凭据。"""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PyInstaller.archive.readers import CArchiveReader

from openresearch.config import Settings


def audit(artifact: Path) -> None:
    settings = Settings()
    secrets = {
        value.get_secret_value().encode()
        for name in type(settings).model_fields
        if hasattr(value := getattr(settings, name), "get_secret_value")
        and len(value.get_secret_value()) >= 6
    }
    password = urlsplit(settings.source_proxy_url.get_secret_value()).password
    if password and len(unquote(password)) >= 6:
        secrets.add(unquote(password).encode())
    archive = CArchiveReader(str(artifact))
    names = {name.replace("\\", "/"): name for name in archive.toc}
    count = 0
    for normalized, name in names.items():
        assert not any(
            part.startswith(".env") or part in {".data", ".venv", "runtime.sqlite3"}
            for part in normalized.split("/")
        ), "private file in release"
        raw = archive.extract(name)
        if raw is not None:
            assert not any(secret in raw for secret in secrets), "configured secret in release data"
            count += 1
        if normalized.endswith(".pyz"):
            modules = archive.open_embedded_archive(name)
            for module in modules.toc:
                raw = modules.extract(module, raw=True)
                if raw is not None:
                    assert not any(secret in raw for secret in secrets), (
                        "configured secret in module"
                    )
                    count += 1
    build = json.loads(archive.extract(names["openresearch/_bundle/build.json"]))
    for relative, expected in build["frontend"].items():
        raw = archive.extract(names["openresearch/_bundle/web/" + relative])
        assert hashlib.sha256(raw).hexdigest() == expected, "bundled frontend mismatch"
    for relative, expected in build["sources"].items():
        if relative.startswith("src/"):
            raw = archive.extract(names[relative.removeprefix("src/")])
            assert hashlib.sha256(raw).hexdigest() == expected, "bundled runtime resource mismatch"
    tool = next(
        name
        for normalized, name in names.items()
        if normalized.startswith("openresearch/_bundle/bin/uv")
    )
    assert hashlib.sha256(archive.extract(tool)).hexdigest() == build["uv"]["sha256"]
    print(
        json.dumps(
            {
                "archive_entries_checked": count,
                "runtime_resources_match": True,
                "configured_secrets_clean": True,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    audit(parser.parse_args().artifact)
