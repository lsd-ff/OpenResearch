"""覆盖部署中的密钥转义、端口隔离、配置保留及失败判断。"""

import argparse
import importlib.util
import json
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deploy_ip", ROOT / "scripts" / "deploy_ip.py")
assert SPEC is not None and SPEC.loader is not None
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def test_environment_preserves_custom_configuration_and_is_idempotent() -> None:
    original = "# 用户配置\nCUSTOM='a=b'\nOPENRESEARCH_API_TOKEN=old\n"
    updated = deploy.render_environment(original, {"OPENRESEARCH_API_TOKEN": "new"})
    assert updated.startswith(original)
    assert deploy.render_environment(updated, {"OPENRESEARCH_API_TOKEN": "new"}) == updated
    assert updated.count(deploy.BEGIN) == 1


@pytest.mark.parametrize("value", ["bad\nvalue", "bad\rvalue", "bad\x00value"])
def test_environment_rejects_injected_lines(value: str) -> None:
    with pytest.raises(deploy.DeploymentError, match="不能包含"):
        deploy.render_environment("", {"OPENRESEARCH_API_TOKEN": value})


def test_docker_compose_preserves_credentials_and_only_publishes_web(tmp_path: Path) -> None:
    if not shutil.which("docker"):
        pytest.skip("需要 Docker Compose CLI，配置解析不需要 Docker daemon")
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True)
    if probe.returncode:
        pytest.skip("Docker Compose CLI 不可用")
    root = tmp_path / "app"
    root.mkdir()
    shutil.copy2(ROOT / "compose.yaml", root / "compose.yaml")
    runtime = deploy.runtime_directory(root)
    runtime.mkdir()
    # 引号、反斜杠、$ 和 ${...} 都不能被误展开，也不能泄漏到测试输出。
    credential = "test-only-$NOT_SET-${OTHER}-'\"-\\path-终"
    values = {
        "COMPOSE_PROJECT_NAME": "deployment-contract-test",
        "OPENRESEARCH_API_TOKEN": credential,
        "OPENRESEARCH_SANDBOX_TOKEN": credential,
        "OPENRESEARCH_LLM_API_KEY": credential,
        "OPENRESEARCH_CORS_ORIGINS": '["http://127.0.0.1:18080"]',
    }
    env_file = runtime / "runtime.env"
    deploy.write_private(env_file, deploy.render_environment("CUSTOM=preserved\n", values))
    deploy.write_private(runtime / "nginx.conf", deploy.render_nginx())
    deploy.write_private(
        runtime / "compose.ip.yaml", deploy.render_compose(runtime, runtime / "web", 18080)
    )
    config = deploy.read_config(deploy.compose_command(root, env_file, deployed=True))
    deploy.verify_config(config, values)
    assert config["name"] == "deployment-contract-test"
    assert not config["services"]["api"].get("ports")
    assert not config["services"]["sandbox"].get("ports")
    assert config["services"]["web"]["ports"][0]["published"] == "18080"
    assert config["services"]["worker"]["environment"]["OPENRESEARCH_MAX_CONCURRENT_TASKS"] == "1"


def test_unmanaged_runtime_directory_is_not_overwritten(tmp_path: Path) -> None:
    runtime = deploy.runtime_directory(tmp_path)
    runtime.mkdir()
    existing = runtime / "runtime.env"
    existing.write_text("important", encoding="utf-8")
    with pytest.raises(deploy.DeploymentError, match="不属于本工具"):
        deploy.prepare_runtime(tmp_path)
    assert existing.read_text(encoding="utf-8") == "important"


def test_original_env_is_unchanged_and_existing_runtime_is_reused(tmp_path: Path) -> None:
    original = tmp_path / ".env"
    original.write_text("CUSTOM=retained\n", encoding="utf-8")
    _, env_file = deploy.prepare_runtime(tmp_path)
    assert tmp_path not in env_file.parents
    deploy.write_private(env_file, "CUSTOM=updated\n")
    assert deploy.prepare_runtime(tmp_path)[1].read_text(encoding="utf-8") == "CUSTOM=updated\n"
    assert original.read_text(encoding="utf-8") == "CUSTOM=retained\n"


def test_existing_credentials_and_project_name_are_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENRESEARCH_DEPLOY_API_KEY", raising=False)
    current = {
        "name": "existing-project",
        "services": {
            "api": {
                "environment": {
                    "OPENRESEARCH_LLM_MODEL": "existing-model",
                    "OPENRESEARCH_LLM_BASE_URL": "https://example.invalid/v1",
                    "OPENRESEARCH_LLM_API_KEY": "existing-key",
                    "OPENRESEARCH_API_TOKEN": "existing-token",
                    "OPENRESEARCH_SANDBOX_TOKEN": "existing-sandbox-token",
                }
            }
        },
    }
    args = argparse.Namespace(
        model=None, llm_base_url=None, non_interactive=True, public_ip="127.0.0.1", port=18080
    )
    values = deploy.deployment_values(args, current)
    assert values["COMPOSE_PROJECT_NAME"] == "existing-project"
    assert values["OPENRESEARCH_API_TOKEN"] == "existing-token"
    assert values["OPENRESEARCH_SANDBOX_TOKEN"] == "existing-sandbox-token"
    assert values["OPENRESEARCH_LLM_API_KEY"] == "existing-key"


def test_busy_port_does_not_stop_another_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        return ""

    monkeypatch.setattr(deploy, "run", execute)
    with socket.socket() as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("0.0.0.0", 0))
        listener.listen()
        with pytest.raises(deploy.DeploymentError, match="已被其他服务使用"):
            deploy.check_port(listener.getsockname()[1], "test")
    assert len(calls) == 1
    assert calls[0][:3] == ["docker", "ps", "-q"]


@pytest.mark.parametrize("authenticated_status", [200, 401])
def test_http_acceptance_requires_both_health_and_authentication(authenticated_status: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html>app</html>"
            content_type = "text/html"
            status = 200
            if self.path == "/api/health":
                body = json.dumps({"ok": True, "sandbox": {"ok": True}}).encode()
                content_type = "application/json"
            if self.path == "/api/hello":
                status = authenticated_status
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if authenticated_status == 401:
            deploy.verify_http(server.server_port)
        else:
            with pytest.raises(deploy.DeploymentError, match="没有被拒绝"):
                deploy.verify_http(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_model_failure_is_not_reported_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy, "run", lambda *args, **kwargs: "503\n")
    with pytest.raises(deploy.DeploymentError, match="模型调用未通过"):
        deploy.verify_model(["docker", "compose"])


def test_sandbox_failure_is_not_reported_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy, "run", lambda *args, **kwargs: "unexpected output\n")
    with pytest.raises(deploy.DeploymentError, match="实际代码执行验收失败"):
        deploy.verify_sandbox(["docker", "compose"])
