"""只收集明确的运行资源；配置、凭据、数据库和项目工作区不进入发行物。"""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import uv
from PyInstaller.__main__ import run as freeze
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(web: Path, tool: Path) -> dict[str, object]:
    sources = sorted((ROOT / "src" / "openresearch").rglob("*.py"))
    sources += sorted((ROOT / "src/openresearch/sandbox/programs").glob("*.txt"))
    sources.append(ROOT / "src/openresearch/sources/stdlib.json")
    sources += [ROOT / "pyproject.toml", ROOT / "uv.lock", Path(__file__)]
    sources.append(ROOT / "packaging" / "entrypoint.py")
    return {
        "format": "openresearch.release/v1",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pyinstaller": version("pyinstaller"),
        "uv": {"version": version("uv"), "sha256": digest(tool)},
        "sources": {path.relative_to(ROOT).as_posix(): digest(path) for path in sources},
        "frontend": {
            path.relative_to(web).as_posix(): digest(path)
            for path in sorted(web.rglob("*"))
            if path.is_file()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the native single-file application")
    parser.add_argument(
        "--skip-frontend", action="store_true", help="use the existing frontend build"
    )
    args = parser.parse_args()
    if not args.skip_frontend:
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError("Node/npm is required on the build machine")
        subprocess.run([npm, "--prefix", "frontend", "run", "build"], cwd=ROOT, check=True)
    web = ROOT / "frontend" / "dist"
    if not (web / "index.html").is_file():
        raise RuntimeError("frontend build is missing index.html")
    tool = Path(uv.find_uv_bin()).resolve()
    work = ROOT / ".data" / "release-build" / sys.platform
    work.mkdir(parents=True, exist_ok=True)
    manifest = work / "build.json"
    manifest.write_text(json.dumps(build_manifest(web, tool), indent=2), encoding="utf-8")
    data = [
        (str(web), "openresearch/_bundle/web"),
        (str(manifest), "openresearch/_bundle"),
        *collect_data_files(
            "openresearch",
            include_py_files=True,
            # 容器程序、迁移和依赖闭包都读取原始源码；冻结的模块字节码不能代替这些文件。
            includes=["**/*.py", "sandbox/programs/*.txt", "sources/stdlib.json"],
        ),
        *collect_data_files("pypdf", include_py_files=True),
    ]
    command = [
        "--onefile",
        "--noconfirm",
        "--noupx",
        "--name",
        "openresearch",
        "--distpath",
        str(ROOT / "dist" / "release"),
        "--workpath",
        str(work / "analysis"),
        "--specpath",
        str(work),
        "--paths",
        str(ROOT / "src"),
        "--add-binary",
        f"{tool}:openresearch/_bundle/bin",
        "--copy-metadata",
        "openresearch",
        "--copy-metadata",
        "uv",
        "--collect-data",
        "certifi",
    ]
    for module in [
        *collect_submodules("alembic", filter=lambda name: not name.startswith("alembic.testing")),
        "uvicorn.logging",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on",
        "sqlalchemy.dialects.sqlite.pysqlite",
    ]:
        command += ["--hidden-import", module]
    for source, destination in data:
        command += ["--add-data", f"{source}:{destination}"]
    # 资源清单超过 Windows 命令行长度时仍可构建；使用官方 Python 入口传递参数。
    freeze([*command, str(ROOT / "packaging" / "entrypoint.py")])
    artifact = (
        ROOT
        / "dist"
        / "release"
        / ("openresearch.exe" if sys.platform == "win32" else "openresearch")
    )
    receipt = {
        **json.loads(manifest.read_text(encoding="utf-8")),
        "artifact_sha256": digest(artifact),
    }
    artifact.with_suffix(".manifest.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    print(json.dumps({"artifact": str(artifact), "sha256": receipt["artifact_sha256"]}))


if __name__ == "__main__":
    main()
