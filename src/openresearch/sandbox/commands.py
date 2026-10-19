import json

from openresearch.errors import ContractError
from openresearch.models.runtime import ExecutionResult
from openresearch.sandbox.base import LogCallback, Sandbox


def command_program(arguments: list[str]) -> str:
    if (
        not arguments
        or len(arguments) > 128
        or not arguments[0]
        or any("\x00" in value or len(value.encode()) > 4096 for value in arguments)
        or len(json.dumps(arguments).encode()) > 32768
    ):
        raise ContractError("command requires bounded literal arguments without NUL")
    # subprocess 只出现在传入容器的程序中；宿主机不运行用户命令，也不做 shell 展开。
    return (
        "import json, os, subprocess, sys\n"
        "os.makedirs('/workspace', exist_ok=True)\n"
        f"arguments = json.loads({json.dumps(arguments)!r})\n"
        "result = subprocess.run(arguments, cwd='/workspace', check=False)\n"
        "sys.exit(result.returncode)\n"
    )


async def execute_command(
    adapter: Sandbox, sandbox_id: str, arguments: list[str], on_log: LogCallback
) -> ExecutionResult:
    return await adapter.execute(sandbox_id, command_program(arguments), on_log)
