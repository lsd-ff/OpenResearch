import io
import zipfile

from openresearch.errors import ContractError


def wheel_sources(wheels: dict[str, bytes], roots: list[str]) -> dict[str, str]:
    excerpts: dict[str, str] = {}
    found: set[str] = set()
    for raw in wheels.values():
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for root in roots:
                prefix = root.replace(".", "/")
                members = [
                    item
                    for item in archive.infolist()
                    if item.filename.startswith(prefix + "/")
                    or item.filename == prefix + ".py"
                    or (item.filename.startswith(prefix + ".") and item.filename.endswith(".so"))
                ]
                if members:
                    found.add(root)
                candidates = sorted(
                    [item for item in members if item.filename.endswith(".py")],
                    key=lambda item: (
                        not any(
                            word in item.filename for word in ("in_memory", "base.py", "retriever")
                        ),
                        len(item.filename),
                    ),
                )
                for item in candidates[:3]:
                    if item.file_size > 512000 or len(excerpts) >= 8:
                        continue
                    excerpts[item.filename] = archive.read(item)[:14000].decode(
                        "utf-8", errors="replace"
                    )
    if found != set(roots):
        raise ContractError("requested framework modules do not exist in verified wheels")
    return excerpts
