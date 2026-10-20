"""用真实目标容器刷新标准库清单；只读取解释器元数据，不安装或导入实验包。"""

import argparse
import json
import tempfile
from contextlib import closing
from pathlib import Path

import docker

PROGRAM = (
    "import json, platform, sys\n"
    "print(json.dumps({'python': platform.python_version(), 'platform': platform.system(), "
    "'machine': platform.machine(), 'libc': platform.libc_ver(), "
    "'modules': sorted(sys.stdlib_module_names)}))\n"
)


def capture(client, version):
    reference = "python:" + version + "-slim-bookworm"
    container = client.containers.run(
        reference,
        ["python", "-c", PROGRAM],
        detach=True,
        network_mode="none",
        read_only=True,
        user="65534:65534",
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        mem_limit="128m",
        nano_cpus=1_000_000_000,
    )
    try:
        if container.wait(timeout=30)["StatusCode"]:
            raise RuntimeError("target interpreter metadata probe failed")
        data = json.loads(container.logs(stdout=True, stderr=False))
        if (
            not data["python"].startswith(version + ".")
            or data["platform"] != "Linux"
            or data["machine"] != "x86_64"
            or data["libc"] != ["glibc", "2.36"]
        ):
            raise RuntimeError("target interpreter does not match the supported runtime")
        data["image"], data["image_id"] = reference, container.attrs["Image"]
        return data
    finally:
        container.remove(force=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src/openresearch/sources/stdlib.json",
    )
    arguments = parser.parse_args()
    with closing(docker.from_env(timeout=30)) as client:
        results = {version: capture(client, version) for version in ("3.11", "3.12", "3.13")}
    destination = arguments.output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=".stdlib-",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                version: {"python": item["python"], "modules": len(item["modules"])}
                for version, item in results.items()
            }
        )
    )


if __name__ == "__main__":
    main()
