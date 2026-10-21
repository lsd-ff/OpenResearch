import asyncio
import secrets
import socket
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

import uvicorn
from pydantic import SecretStr

from openresearch.config import Settings
from openresearch.sandbox.service import create_sandbox_app


class ManagedServer(uvicorn.Server):
    async def serve(self, sockets: list[socket.socket] | None = None) -> None:
        try:
            await super().serve(sockets)
        except SystemExit as error:
            # Uvicorn 启动错误不能以后台 Task 的 SystemExit 打断整个循环，清理仍须执行。
            raise RuntimeError(f"local HTTP startup failed (exit {error.code})") from error

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # 同进程多个 HTTP 服务只由最外层安装一次退出信号，避免互相覆盖。
        yield


@dataclass(frozen=True)
class LocalBackend:
    settings: Settings
    future: asyncio.Task[None] | None = None


async def stop_server(server: uvicorn.Server, future: asyncio.Task[None]) -> None:
    server.should_exit = True
    try:
        async with asyncio.timeout(30):
            await asyncio.shield(future)
    except TimeoutError:
        future.cancel()
        await asyncio.gather(future, return_exceptions=True)
        raise RuntimeError("local server shutdown timed out") from None


@asynccontextmanager
async def local_backend(settings: Settings) -> AsyncIterator[LocalBackend]:
    if settings.sandbox_backend in {"http", "opensandbox"}:
        yield LocalBackend(settings)
        return
    if "sandbox_url" in settings.model_fields_set:
        yield LocalBackend(settings.model_copy(update={"sandbox_backend": "http"}))
        return
    token = settings.sandbox_token or SecretStr(secrets.token_urlsafe(32))
    service_settings = settings.model_copy(
        update={
            "sandbox_token": token,
            "sandbox_fallback_enabled": (
                settings.sandbox_backend == "auto" and settings.sandbox_fallback_enabled
            ),
        }
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # 持有同一个已绑定句柄直到退出，不采用先探测空闲端口再重新绑定的竞态方式。
        listener.bind(("127.0.0.1", 0))
        server = ManagedServer(
            uvicorn.Config(
                create_sandbox_app(service_settings),
                log_config=None,
                lifespan="on",
                loop="asyncio",
                http="h11",
                timeout_graceful_shutdown=10,
            )
        )
        future = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(15):
                while not server.started:
                    if future.done():
                        future.result()
                        raise RuntimeError("local sandbox stopped before startup")
                    await asyncio.sleep(0.02)
            yield LocalBackend(
                settings.model_copy(
                    update={
                        "sandbox_backend": "http",
                        "sandbox_url": f"http://127.0.0.1:{listener.getsockname()[1]}",
                        "sandbox_token": token,
                    }
                ),
                future,
            )
        finally:
            await stop_server(server, future)
    finally:
        listener.close()
