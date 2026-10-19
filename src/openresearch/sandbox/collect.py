from pathlib import Path

from pydantic import ValidationError

from openresearch.errors import SandboxError
from openresearch.models.output_files import OutputFiles
from openresearch.sandbox.python_files import ignore_log
from openresearch.sandbox.session import RunningSandbox

COLLECTION_MARKER = "# OpenResearch artifact collector\n"


async def collect_files(session: RunningSandbox, max_bytes: int) -> OutputFiles:
    source = (Path(__file__).parent / "programs" / "collect_outputs.py").read_text(encoding="utf-8")
    limit = min(max_bytes // 3, 1048576)
    program = COLLECTION_MARKER + source + f"\nprint(collection_json({limit}))\n"
    result = await session.adapter.execute(session.id, program, ignore_log)
    if result.exit_code != 0:
        raise SandboxError("output files exceed limits or contain forbidden links or file types")
    try:
        return OutputFiles.model_validate_json(result.stdout)
    except ValidationError:
        raise SandboxError("sandbox output files failed checksum or format validation") from None
