#!/usr/bin/env python3
"""在已有 Docker 的 Linux 服务器上一键部署；仅依赖 Python 3.9+ 标准库。"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPOSITORY = "https://github.com/lsd-ff/OpenResearch-Python.git"
BEGIN = "# BEGIN OPENRESEARCH IP DEPLOYMENT"
END = "# END OPENRESEARCH IP DEPLOYMENT"
MARKER = "openresearch-ip-v1"


class DeploymentError(RuntimeError):
    """可直接展示给运维人员、不会携带密钥的部署错误。"""


def runtime_directory(root: Path) -> Path:
    # 凭据放在源码目录之外，兼容旧版仓库，也不会进入 Docker 构建上下文。
    return root.with_name(root.name + "-deployment")


def run(command: list[str], *, capture: bool = False, **kwargs: object) -> str:
    # Compose 的宿主环境优先于 env 文件；过滤同名前缀，确保落盘配置就是实际配置。
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENRESEARCH_", "COMPOSE_"))
    }
    result = subprocess.run(
        command,
        env=environment,
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=capture,
        **kwargs,
    )
    return result.stdout if capture else ""


def write_private(path: Path, text: str) -> None:
    if path.is_symlink():
        raise DeploymentError("拒绝覆盖符号链接：" + str(path))
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    os.replace(temporary, path)
    path.chmod(0o600)


def render_environment(original: str, values: dict[str, str]) -> str:
    # 保留用户原来的所有配置；只替换本工具自己的尾部配置块，不执行 source .env。
    pattern = rf"(?ms)^{re.escape(BEGIN)}\n.*?^{re.escape(END)}\n?"
    original = re.sub(pattern, "", original).rstrip()
    lines = [original, "", BEGIN]
    for key, value in values.items():
        if any(character in value for character in ("\n", "\r", "\x00")):
            raise DeploymentError("配置值不能包含换行或空字符：" + key)
        # 双引号转义交给 JSON；$$ 防止 Compose 把密钥里的 $ 当作变量展开。
        quoted = json.dumps(value.replace("$", "$$"), ensure_ascii=False)
        lines.append(key + "=" + quoted)
    return "\n".join([*lines, END, ""])


def render_nginx() -> str:
    return """server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;
    client_max_body_size 40m;
    resolver 127.0.0.11 valid=10s ipv6=off;

    location ~ ^/(api(?:/|$)|docs(?:/|$)|redoc(?:/|$)|openapi\\.json$|ping$) {
        set $api_backend http://api:8080;
        proxy_pass $api_backend$request_uri;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_connect_timeout 10s;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
        proxy_buffering off;
        proxy_cache off;
        gzip off;
    }
    location = /index.html { add_header Cache-Control "no-cache"; }
    location / { try_files $uri $uri/ /index.html; }
    location ~ /\\. { deny all; }
}
"""


def render_compose(runtime: Path, frontend: Path, port: int) -> str:
    configuration = json.dumps(str(runtime / "nginx.conf"))
    static_files = json.dumps(str(frontend))
    return f"""x-ip-defaults: &ip-defaults
  restart: unless-stopped
  init: true
  stop_grace_period: 60s
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
services:
  api:
    <<: *ip-defaults
    ports: !reset []
    mem_limit: 1g
    environment:
      FORWARDED_ALLOW_IPS: "*"
      OPENRESEARCH_REQUIRE_APPROVAL: "true"
      OPENRESEARCH_MAX_CONCURRENT_TASKS: "1"
      OPENRESEARCH_CORS_ORIGINS: '${{OPENRESEARCH_CORS_ORIGINS}}'
  worker:
    <<: *ip-defaults
    mem_limit: 2g
    environment:
      OPENRESEARCH_REQUIRE_APPROVAL: "true"
      OPENRESEARCH_MAX_CONCURRENT_TASKS: "1"
  sandbox:
    <<: *ip-defaults
    mem_limit: 768m
    environment:
      OPENRESEARCH_SANDBOX_FALLBACK_ENABLED: "false"
  web:
    <<: *ip-defaults
    image: nginx:stable-alpine
    mem_limit: 128m
    ports:
      - "0.0.0.0:{port}:80"
    volumes:
      - type: bind
        source: {configuration}
        target: /etc/nginx/conf.d/default.conf
        read_only: true
      - type: bind
        source: {static_files}
        target: /usr/share/nginx/html
        read_only: true
    depends_on:
      api:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O /dev/null http://127.0.0.1/ping || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 6
"""


def compose_command(root: Path, env_file: Path, *, deployed: bool = False) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(root),
        "--env-file",
        str(env_file),
        "-f",
        str(root / "compose.yaml"),
    ]
    overrides = [root / "compose.override.yaml", root / "compose.override.yml"]
    existing = [path for path in overrides if path.exists()]
    if len(existing) > 1:
        raise DeploymentError("存在两个 Compose override 文件，请先明确保留哪一个。")
    if existing:
        command.extend(["-f", str(existing[0])])
    if deployed:
        command.extend(["-f", str(runtime_directory(root) / "compose.ip.yaml")])
    return command


def read_config(command: list[str]) -> dict:
    try:
        config = json.loads(run([*command, "config", "--format", "json"], capture=True))
    except (subprocess.CalledProcessError, ValueError) as error:
        raise DeploymentError("Compose 配置无效，请检查配置文件及 Compose 版本。") from error
    # config 输出为可再次加载的 Compose 文档，会把运行值中的 $ 转回 $$。
    # 还原后才能复用已有密钥，不能直接把序列化结果当成容器环境值。
    for service in config.get("services", {}).values():
        for key, value in service.get("environment", {}).items():
            if isinstance(value, str):
                service["environment"][key] = value.replace("$$", "$")
    return config


def prepare_repository(root: Path, ref: str) -> None:
    if root.is_symlink() or str(root) != str(root.resolve()) or len(root.parts) < 3:
        raise DeploymentError("部署目录必须是普通绝对目录，不能是系统顶层目录或符号链接。")
    if not root.exists():
        root.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth", "1", "--branch", "main", REPOSITORY, str(root)])
        if ref != "main":
            run(["git", "-C", str(root), "fetch", "--depth", "1", "origin", ref])
            run(["git", "-C", str(root), "checkout", "--detach", "FETCH_HEAD"])
    if not (root / ".git").exists() or not (root / "compose.yaml").is_file():
        raise DeploymentError("部署目录已存在，但不是有效的项目仓库；不会覆盖该目录。")
    remote = run(["git", "-C", str(root), "remote", "get-url", "origin"], capture=True)
    if remote.strip().removesuffix(".git") != REPOSITORY.removesuffix(".git"):
        raise DeploymentError("已有目录的 Git origin 与目标仓库不一致；不会覆盖。")
    if run(["git", "-C", str(root), "diff", "HEAD", "--name-only"], capture=True).strip():
        raise DeploymentError("仓库存在未提交的代码改动，请先保存；不会自动重置。")
    print("已有仓库使用当前代码版本，不自动拉取或切换分支。", flush=True)


def prepare_runtime(root: Path) -> tuple[Path, Path]:
    runtime = runtime_directory(root)
    marker = runtime / "managed-by"
    if runtime.is_symlink() or (runtime.exists() and not marker.is_file()):
        raise DeploymentError("运行配置目录已存在且不属于本工具；不会覆盖。")
    if marker.exists() and marker.read_text(encoding="utf-8").strip() != MARKER:
        raise DeploymentError("运行配置版本无法识别；不会覆盖。")
    runtime.mkdir(mode=0o700, exist_ok=True)
    runtime.chmod(0o700)
    write_private(marker, MARKER + "\n")
    env_file = runtime / "runtime.env"
    if not env_file.exists():
        source = root / ".env"
        text = source.read_text(encoding="utf-8") if source.exists() else ""
        write_private(env_file, text)
    return runtime, env_file


def deployment_values(args: argparse.Namespace, config: dict) -> dict[str, str]:
    current = config["services"]["api"].get("environment", {})
    model = args.model or current.get("OPENRESEARCH_LLM_MODEL", "")
    if not model and not args.non_interactive:
        model = input("模型名称：").strip()
    api_key = os.environ.get("OPENRESEARCH_DEPLOY_API_KEY") or current.get(
        "OPENRESEARCH_LLM_API_KEY", ""
    )
    if not api_key and not args.non_interactive:
        if not sys.stdin.isatty():
            raise DeploymentError("请先下载脚本再运行，模型密钥需要在交互终端输入。")
        api_key = getpass.getpass("请输入模型 API Key（输入不显示）：").strip()
    if not model or not api_key:
        raise DeploymentError("模型名称和 API Key 不能为空。")
    base_url = args.llm_base_url or current.get("OPENRESEARCH_LLM_BASE_URL", "")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username:
        raise DeploymentError("模型地址必须是无内嵌凭据的 HTTP/HTTPS URL。")
    return {
        "COMPOSE_PROJECT_NAME": config["name"],
        "OPENRESEARCH_LLM_PROVIDER": "openai",
        "OPENRESEARCH_LLM_BASE_URL": base_url,
        "OPENRESEARCH_LLM_MODEL": model,
        "OPENRESEARCH_LLM_API_KEY": api_key,
        "OPENRESEARCH_API_TOKEN": current.get("OPENRESEARCH_API_TOKEN") or secrets.token_hex(32),
        "OPENRESEARCH_SANDBOX_TOKEN": current.get("OPENRESEARCH_SANDBOX_TOKEN")
        or secrets.token_hex(32),
        "OPENRESEARCH_LLM_TIMEOUT_SECONDS": "120",
        "OPENRESEARCH_SANDBOX_MEMORY": "1g",
        "OPENRESEARCH_SOURCE_DOWNLOAD_TIMEOUT_SECONDS": "180",
        "OPENRESEARCH_CORS_ORIGINS": json.dumps([f"http://{args.public_ip}:{args.port}"]),
    }


def check_port(port: int, project: str) -> None:
    try:
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", port))
        return
    except OSError:
        containers = run(["docker", "ps", "-q", "--filter", f"publish={port}"], capture=True)
    for container_id in containers.split():
        info = json.loads(run(["docker", "inspect", container_id], capture=True))[0]
        labels = info.get("Config", {}).get("Labels") or {}
        if (
            labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != "web"
        ):
            raise DeploymentError(f"端口 {port} 已被其他容器使用，请用 --port 指定空闲端口。")
    if not containers.strip():
        raise DeploymentError(f"端口 {port} 已被其他服务使用，请用 --port 指定空闲端口。")


def verify_config(config: dict, values: dict[str, str]) -> None:
    services = config["services"]
    if services["api"].get("ports") or services["sandbox"].get("ports"):
        raise DeploymentError("API 或沙箱仍发布了宿主机端口，停止部署以避免意外公开。")
    actual = services["api"]["environment"]
    for key in ("OPENRESEARCH_LLM_API_KEY", "OPENRESEARCH_API_TOKEN", "OPENRESEARCH_SANDBOX_TOKEN"):
        if actual.get(key) != values[key]:
            raise DeploymentError("配置转义或自定义 Compose 配置发生冲突：" + key)


def backup_configuration(runtime: Path) -> None:
    backup = runtime / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3))
    backup.mkdir(parents=True, mode=0o700)
    for name in ("runtime.env", "compose.ip.yaml", "nginx.conf", "access-token.txt"):
        source = runtime / name
        if source.is_file():
            shutil.copy2(source, backup / name)
            (backup / name).chmod(0o600)


def verify_http(port: int) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    address = f"http://127.0.0.1:{port}"
    with opener.open(address + "/", timeout=15) as response:
        if response.status != 200 or "text/html" not in response.headers.get("Content-Type", ""):
            raise DeploymentError("前端页面验收失败。")
    with opener.open(address + "/api/health", timeout=15) as response:
        health = json.load(response)
    if not health.get("ok") or not health.get("sandbox", {}).get("ok"):
        raise DeploymentError("API 或沙箱健康检查失败。")
    try:
        opener.open(address + "/api/hello", timeout=15).close()
    except urllib.error.HTTPError as error:
        if error.code == 401:
            return
        raise DeploymentError("API 认证验收失败。") from error
    raise DeploymentError("未认证请求没有被拒绝，停止验收。")


def verify_model(command: list[str]) -> None:
    probe = """import httpx
from openresearch.config import Settings
s = Settings()
r = httpx.post('http://127.0.0.1:8080/api/chat',
    headers={'Authorization': 'Bearer ' + s.api_token.get_secret_value()},
    json={'message': '请只回复：模型连接成功'}, timeout=150)
print(r.status_code)
"""
    status = run(
        [*command, "exec", "-T", "api", "/app/.venv/bin/python", "-"],
        capture=True,
        input=probe,
        timeout=170,
    ).strip()
    if status != "200":
        raise DeploymentError("应用已启动，但模型调用未通过；请检查接口、模型、密钥和额度。")


def verify_sandbox(command: list[str]) -> None:
    probe = """import asyncio
from openresearch.config import Settings
from openresearch.models.runtime import new_id
from openresearch.sandbox.factory import build_sandbox
async def main():
    backend = build_sandbox(Settings())
    identifier = None
    async def on_log(stream, message):
        pass
    try:
        identifier = await backend.create(new_id(), new_id())
        result = await backend.execute(identifier,
            'assert sum([1, 2, 3]) == 6\\nprint("DEPLOY_SANDBOX_OK")', on_log)
        assert result.exit_code == 0 and 'DEPLOY_SANDBOX_OK' in result.stdout
        print('DEPLOY_SANDBOX_OK')
    finally:
        try:
            if identifier is not None:
                await backend.close(identifier)
        finally:
            await backend.shutdown()
asyncio.run(main())
"""
    result = run(
        [*command, "exec", "-T", "api", "/app/.venv/bin/python", "-"],
        capture=True,
        input=probe,
        timeout=120,
    )
    if result.strip() != "DEPLOY_SANDBOX_OK":
        raise DeploymentError("沙箱实际代码执行验收失败。")


def deploy(args: argparse.Namespace) -> None:
    root = Path(args.directory).absolute()
    prepare_repository(root, args.ref)
    runtime, env_file = prepare_runtime(root)
    base = compose_command(root, env_file)
    previous = read_config(base)
    if "web" in previous["services"]:
        raise DeploymentError("已有 Compose 使用 web 服务名称，请先调整名称，避免覆盖。")
    values = deployment_values(args, previous)
    check_port(args.port, values["COMPOSE_PROJECT_NAME"])
    backup_configuration(runtime)
    write_private(env_file, render_environment(env_file.read_text(encoding="utf-8"), values))
    write_private(runtime / "access-token.txt", values["OPENRESEARCH_API_TOKEN"] + "\n")
    write_private(runtime / "nginx.conf", render_nginx())
    frontend = runtime / ("web-" + secrets.token_hex(6))
    frontend.mkdir(mode=0o755)
    write_private(runtime / "compose.ip.yaml", render_compose(runtime, frontend, args.port))
    command = compose_command(root, env_file, deployed=True)
    verify_config(read_config(command), values)

    print("拉取沙箱和 Nginx 镜像，构建应用；首次执行可能需要较长时间。", flush=True)
    sandbox_image = previous["services"]["sandbox"]["environment"].get(
        "OPENRESEARCH_SANDBOX_IMAGE", "python:3.12-slim"
    )
    run(["docker", "pull", sandbox_image])
    run([*command, "pull", "web"])
    run([*command, "build", "api"])
    run([*command, "up", "-d", "--wait", "--wait-timeout", "180", "api", "worker", "sandbox"])
    run([*command, "cp", "api:/app/frontend/dist/.", str(frontend)])
    if not (frontend / "index.html").is_file():
        raise DeploymentError("构建结果中没有前端首页；不会发布空白页面。")
    # Nginx worker 必须能读取静态目录；密钥文件始终留在独立的 0700 运行目录内。
    for path in [frontend, *frontend.rglob("*")]:
        if path.is_symlink():
            raise DeploymentError("前端产物包含意外的符号链接。")
        path.chmod(0o755 if path.is_dir() else 0o644)
    run([*command, "run", "--rm", "--no-deps", "web", "nginx", "-t"])
    run([*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180", "web"])
    verify_http(args.port)
    print("验证真实沙箱代码执行及清理。", flush=True)
    verify_sandbox(command)
    if not args.skip_model_check:
        print("验证真实模型调用。", flush=True)
        verify_model(command)
    print(f"\n应用和沙箱健康检查通过。访问：http://{args.public_ip}:{args.port}")
    print("模型检查：" + ("已跳过，尚未验证" if args.skip_model_check else "通过"))
    print("公网可达性还取决于腾讯云安全组；请放行上述 TCP 端口，建议限制来源 IP。")
    print("网页连接令牌保存在：" + str(runtime / "access-token.txt"))
    print("原 .env、已有网站及业务数据卷均未删除。")
    print("查看状态：python3 " + str(Path(__file__).resolve()) + " status --directory " + str(root))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("deploy", "status", "logs", "restart"), nargs="?", default="deploy"
    )
    parser.add_argument("--public-ip", type=ipaddress.IPv4Address)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--directory", default="/opt/openresearch")
    parser.add_argument("--ref", default="main", help="仅首次克隆时选择代码版本")
    parser.add_argument("--llm-base-url")
    parser.add_argument("--model")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--skip-model-check", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        raise DeploymentError("请在 Linux 服务器的 root 终端运行。")
    if not 1 <= args.port <= 65535:
        raise DeploymentError("端口必须在 1 到 65535 之间。")
    for executable in ("docker", "git"):
        if not shutil.which(executable):
            raise DeploymentError("请先安装：" + executable)
    run(["docker", "info"], capture=True)
    run(["docker", "compose", "version"], capture=True)

    import fcntl

    with Path("/var/lock/openresearch-ip-deploy.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DeploymentError("已有部署正在运行，请等待其完成。") from error
        if args.action == "deploy":
            if not args.public_ip:
                raise DeploymentError("请用 --public-ip 指定服务器公网 IPv4。")
            deploy(args)
        else:
            root = Path(args.directory).absolute()
            env_file = runtime_directory(root) / "runtime.env"
            if not env_file.is_file():
                raise DeploymentError("未找到本脚本的部署配置。")
            command = compose_command(root, env_file, deployed=True)
            operations = {
                "status": ["ps"],
                "logs": ["logs", "--tail=100"],
                "restart": ["up", "-d", "--wait"],
            }
            run([*command, *operations[args.action]])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (DeploymentError, subprocess.CalledProcessError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, DeploymentError) else type(error).__name__
        print("\n部署未完成：" + message, file=sys.stderr)
        print("已保留现场和数据卷。可使用脚本的 status / logs 命令检查。", file=sys.stderr)
        sys.exit(1)
