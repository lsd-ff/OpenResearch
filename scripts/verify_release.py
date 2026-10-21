"""在独立目录和无 Python/Node/uv 的 PATH 中验收真实发行物；需要 Docker 与 PyPI。"""

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import docker
import httpx
from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[1]
NATIVE_CODE = """import sys
assert sum([2, 3]) == 5
assert list(reversed([1, 2])) == [2, 1]
assert sys.platform == 'linux'
print('RELEASE_NATIVE_ASSERTIONS 3', sys.platform)
"""
CODE = """import sys
from pathlib import Path
from importlib.metadata import version
import blinker

seen = []
def receive(sender):
    seen.append(sender)
event = blinker.signal('release-actual')
event.connect(receive)
event.send('actual-container')
assert seen == ['actual-container']
assert sys.platform == 'linux'
assert version('blinker').startswith('1.')
Path('/workspace/artifacts').mkdir(parents=True, exist_ok=True)
Path('/workspace/artifacts/release.svg').write_text(
    '<svg xmlns="http://www.w3.org/2000/svg">'
    '<text y="20">actual release</text></svg>')
print('RELEASE_ASSERTIONS 3', version('blinker'), sys.platform)
"""


class ReleaseProcess:
    def __init__(self, artifact: Path, root: Path) -> None:
        program = root / "program"
        program.mkdir()
        self.executable = program / artifact.name
        shutil.copy2(artifact, self.executable)
        self.data = program / ".data"
        self.cwd = root / "different-working-directory"
        self.cwd.mkdir()
        empty_path = root / "empty-path"
        empty_path.mkdir()
        self.environment = {
            key: value
            for key, value in os.environ.items()
            # 只允许显式来源网络配置穿过隔离边界，模型与应用私有设置继续由空环境隔离。
            if key.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "HOME",
                "OPENRESEARCH_SOURCE_PROXY_URL",
                "OPENRESEARCH_SOURCE_DOWNLOAD_TIMEOUT_SECONDS",
            }
            or key.upper().startswith("DOCKER_")
        }
        with socket.create_server(("127.0.0.1", 0)) as listener:
            self.port = listener.getsockname()[1]
        self.environment.update(
            PATH=str(empty_path),
            OPENRESEARCH_PORT=str(self.port),
            OPENRESEARCH_API_TOKEN="release-test-only",
            OPENRESEARCH_REQUIRE_APPROVAL="true",
        )
        self.process = None
        self.log = (root / "process.log").open("ab")
        self.headers = {
            "Authorization": "Bearer release-test-only",
            "X-User-Id": "release-" + uuid4().hex,
            "X-Session-Id": uuid4().hex,
        }

    async def start(self):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.process = await asyncio.to_thread(
            subprocess.Popen,
            [str(self.executable), "--exit-on-stdin-close"],
            cwd=self.cwd,
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=self.log,
            stderr=self.log,
            creationflags=flags,
        )
        client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{self.port}",
            headers=self.headers,
            trust_env=False,
            timeout=30,
        )
        try:
            async with asyncio.timeout(45):
                while True:
                    if self.process.poll() is not None:
                        raise RuntimeError("release process exited before startup; see process.log")
                    try:
                        if (await client.get("/ping")).status_code == 200:
                            return client
                    except httpx.ConnectError:
                        pass
                    await asyncio.sleep(0.1)
        except BaseException:
            await client.aclose()
            raise

    async def stop(self, *, use_sigterm=False):
        if self.process is None:
            return
        if use_sigterm:
            assert sys.platform != "win32", "SIGTERM acceptance requires a POSIX host"
            # 保持标准输入打开，单独证明冻结程序能转发并优雅处理 Linux 终止信号。
            self.process.terminate()
        elif self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        code = await asyncio.to_thread(self.process.wait, 35)
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        assert code == 0, f"release exited {code}; see process.log"
        async with httpx.AsyncClient(trust_env=False) as client:
            try:
                await client.get(f"http://127.0.0.1:{self.port}/ping")
            except httpx.ConnectError:
                return
        raise AssertionError("release API survived process shutdown")

    def inventory(self):
        namespace = (self.data / "runtime-id").read_text(encoding="ascii")
        client = docker.from_env()
        try:
            return client.containers.list(
                all=True, filters={"label": f"openresearch.namespace={namespace}"}
            )
        finally:
            client.close()


async def completed(client, identifier):
    async with asyncio.timeout(240):
        while True:
            response = await client.get(f"/api/plans/{identifier}")
            response.raise_for_status()
            plan = response.json()["plan_graph"]
            if plan["status"] in {"completed", "failed", "canceled"}:
                assert plan["status"] == "completed", [
                    (node["type"], node["error"]) for node in plan["nodes"]
                ]
                assert all(node["run_count"] == 1 for node in plan["nodes"])
                return plan
            await asyncio.sleep(0.1)


async def approve_and_run(client, identifier):
    assert (await client.post(f"/api/plans/{identifier}/execute")).status_code == 409
    assert (await client.post(f"/api/plans/{identifier}/approve")).status_code == 200
    assert (await client.post(f"/api/plans/{identifier}/execute")).status_code == 202
    return await completed(client, identifier)


async def artifact_json(client, plan, key):
    artifact = plan["artifacts"][key]
    response = await client.get(f"/api/plans/{plan['id']}/artifacts/{artifact['id']}/content")
    response.raise_for_status()
    return response.json()


async def run_code(client, *, dependencies=False):
    if dependencies:
        response = await client.post(
            "/api/execute",
            json={
                "task_type": "execute_code",
                "assigned_to": "coder_agent",
                "task_description": "实际执行给定断言，安装声明的 blinker>=1.9,<2，禁止修改代码",
                "inputs": {"code": CODE, "requirements": ["blinker>=1.9,<2"]},
            },
        )
        assert response.status_code == 202
        identifier = response.json()["plan_id"]
    else:
        response = await client.post(
            "/api/plan",
            json={
                "intent": "实际运行给定断言，禁止修改代码",
                "workflow": "code",
                "code": NATIVE_CODE,
            },
        )
        response.raise_for_status()
        identifier = response.json()["plan_graph"]["id"]
    plan = await approve_and_run(client, identifier)
    report = await artifact_json(client, plan, "code_recovery_report")
    assert report["success"] and report["original_code_sha256"] == report["final_code_sha256"]
    attempt = report["attempts"][-1]
    stamp = "RELEASE_ASSERTIONS 3" if dependencies else "RELEASE_NATIVE_ASSERTIONS 3"
    assert stamp in attempt["output"]["stdout"]
    assert attempt["container_image_id"].startswith("sha256:")
    if dependencies:
        assert "blinker" in attempt["environment"]["installed"]
    return plan, report


def verify_manifest(artifact: Path):
    manifest = json.loads(artifact.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == manifest["artifact_sha256"]
    for relative, expected in manifest["sources"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, relative
    return manifest


async def verify(artifact: Path):
    manifest = await asyncio.to_thread(verify_manifest, artifact)
    root = ROOT / ".data" / "release-acceptance" / uuid4().hex
    root.mkdir(parents=True)
    runtime = await asyncio.to_thread(ReleaseProcess, artifact, root)
    client = None
    try:
        client = await runtime.start()
        html = (await client.get("/")).text
        assert "<html" in html
        assert (await client.get("/workspace/history")).text == html
        assert (await client.get("/api/missing")).status_code == 404
        for url in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html):
            response = await client.get(url)
            response.raise_for_status()
            assert (
                hashlib.sha256(response.content).hexdigest()
                == manifest["frontend"][url.lstrip("/")]
            )
        health = (await client.get("/api/health")).json()
        assert health["ok"] and health["sandbox"]["strategy"] == "http", health
        print("release: embedded UI/API/sandbox ready", flush=True)
        plan, report = await run_code(client)
        (root / "code-receipt.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        dependency, report = await run_code(client, dependencies=True)
        (root / "dependency-receipt.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(
            "release: bundled uv, actual dependency and three container assertions passed",
            flush=True,
        )
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        stream = io.BytesIO()
        writer.write(stream)
        raw = stream.getvalue()
        upload = await client.post(
            "/api/uploads", files={"file": ("blank-functional.pdf", raw, "application/pdf")}
        )
        upload.raise_for_status()
        assert upload.json()["sha256"] == hashlib.sha256(raw).hexdigest()
        response = await client.post(
            "/api/plan",
            json={
                "intent": "解析这份空白功能样例 PDF",
                "workflow": "paper",
                "attachments": [upload.json()["id"]],
            },
        )
        response.raise_for_status()
        pdf = await approve_and_run(client, response.json()["plan_graph"]["id"])
        corpus = await artifact_json(client, pdf, "parsed_paper")
        assert corpus["documents"][0]["pages"][0]["needs_ocr"]
        assert corpus["documents"][0]["source"]["sha256"] == upload.json()["sha256"]
        pending = await client.post("/api/plan", json={"intent": "smoke", "workflow": "code"})
        saved = pending.json()["plan_graph"]
        await client.aclose()
        client = None
        await runtime.stop()
        assert not await asyncio.to_thread(runtime.inventory)
        assert not (runtime.cwd / ".data").exists()
        client = await runtime.start()
        for original in [plan, dependency, pdf, saved]:
            reread = (await client.get(f"/api/plans/{original['id']}")).json()["plan_graph"]
            assert reread == original
        await client.aclose()
        client = None
        await runtime.stop(use_sigterm=sys.platform != "win32")
        assert not await asyncio.to_thread(runtime.inventory)
        summary = {
            "passed": True,
            "artifact_sha256": manifest["artifact_sha256"],
            "code_plan": plan["id"],
            "dependency_plan": dependency["id"],
            "pdf_plan": pdf["id"],
            "restart_did_not_rerun": True,
            "shutdown_modes": [
                "stdin_close",
                "sigterm" if sys.platform != "win32" else "stdin_close",
            ],
            "active_containers": 0,
            "scope": (
                "Actual frozen executable, empty PATH, embedded UI/API/Worker/local HTTP sandbox, "
                "actual PyPI resolution and Docker; blank PDF is a functional fixture."
            ),
        }
        (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps({"receipt": str(root), **summary}), flush=True)
    finally:
        if client is not None:
            await client.aclose()
        if runtime.process is not None and runtime.process.poll() is None:
            try:
                await runtime.stop()
            except (AssertionError, subprocess.TimeoutExpired):
                runtime.process.kill()
                await asyncio.to_thread(runtime.process.wait)
        runtime.log.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    asyncio.run(verify(args.artifact.resolve()))


if __name__ == "__main__":
    main()
