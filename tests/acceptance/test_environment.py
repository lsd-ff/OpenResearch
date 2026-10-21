import hashlib
import io
import json
import zipfile

from test_docker_smoke import pytestmark as pytestmark

from openresearch.research.environment import installation_program
from openresearch.sandbox.docker import DockerSandbox


def conditional_wheel() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        prefix = "conditional-1.0.dist-info/"
        archive.writestr(
            prefix + "WHEEL",
            "Wheel-Version: 1.0\nGenerator: acceptance\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(
            prefix + "METADATA",
            "Metadata-Version: 2.1\nName: conditional\nVersion: 1.0\n"
            "Requires-Dist: missing; python_full_version >= '3.12.1'\n",
        )
        archive.writestr(prefix + "RECORD", "")
    return output.getvalue()


async def test_actual_python_markers_require_complete_environment(settings) -> None:
    sandbox = DockerSandbox(settings)
    identifier = await sandbox.create("conditional-environment", "conditional-attempt")
    raw = conditional_wheel()
    requirement = f"conditional==1.0 --hash=sha256:{hashlib.sha256(raw).hexdigest()}"

    async def log(stream: str, message: str) -> None:
        pass

    try:
        await sandbox.put_files(
            identifier,
            {
                "wheels/conditional-1.0-py3-none-any.whl": raw,
                "requirements.lock": requirement.encode(),
            },
        )
        code = installation_program()
        result = await sandbox.execute(identifier, code, log)
        assert result.exit_code != 0
        assert "unsatisfied dependency" in result.stderr
    finally:
        await sandbox.close(identifier)


async def test_binary_installation_scratch_does_not_exhaust_the_small_input_tmpfs(settings):
    settings.sandbox_tmpfs_size_mb = 16
    settings.sandbox_workspace_size_mb = 64
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        prefix = "scratch_probe-1.0.dist-info/"
        archive.writestr(
            prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        archive.writestr(
            prefix + "METADATA", "Metadata-Version: 2.1\nName: scratch-probe\nVersion: 1.0\n"
        )
        archive.writestr(prefix + "RECORD", "")
        archive.writestr("scratch_probe/payload.bin", b"x" * (20 * 1024 * 1024))
    raw = output.getvalue()
    sandbox = DockerSandbox(settings)
    identifier = await sandbox.create("large-wheel", "small-input-tmpfs")
    requirement = f"scratch-probe==1.0 --hash=sha256:{hashlib.sha256(raw).hexdigest()}"

    async def log(stream, message):
        pass

    try:
        await sandbox.put_files(
            identifier,
            {
                "wheels/scratch_probe-1.0-py3-none-any.whl": raw,
                "requirements.lock": requirement.encode(),
            },
        )
        code = installation_program()
        result = await sandbox.execute(identifier, code, log)
        assert result.exit_code == 0, result.stderr
        installed = json.loads(await sandbox.get_file(identifier, "environment.json", 65536))
        assert installed["installed"] == {"scratch-probe": "1.0"}
    finally:
        await sandbox.close(identifier)


async def test_native_extensions_can_map_from_workspace_while_tmp_stays_noexec(settings):
    sandbox = DockerSandbox(settings)
    identifier = await sandbox.create("native-extensions", "workspace-mapping")

    async def log(stream, message):
        pass

    program = (
        "import _csv, ctypes, json, shutil\n"
        "results = {}\n"
        "for directory in ['/workspace', '/tmp']:\n"
        "    path = directory + '/native-probe.so'\n"
        "    shutil.copyfile(_csv.__file__, path)\n"
        "    try:\n"
        "        ctypes.CDLL(path)\n"
        "        results[directory] = True\n"
        "    except OSError:\n"
        "        results[directory] = False\n"
        "print(json.dumps(results))\n"
    )
    try:
        result = await sandbox.execute(identifier, program, log)
        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout) == {"/workspace": True, "/tmp": False}
    finally:
        await sandbox.close(identifier)
