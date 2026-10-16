"""跨平台执行工程质量门禁，每个检查失败即停止。"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    ("ruff", "check", "."),
    ("ruff", "format", "--check", "."),
    ("mypy", "src"),
    ("pytest", "-m", "not docker"),
)


def main() -> None:
    for command in COMMANDS:
        print(f"Checking: {' '.join(command)}", flush=True)
        subprocess.run(["uv", "run", "--locked", *command], cwd=PROJECT_ROOT, check=True)
    subprocess.run(["uv", "build"], cwd=PROJECT_ROOT, check=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
