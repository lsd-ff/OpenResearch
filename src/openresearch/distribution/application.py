import asyncio
import signal
import socket
import sys
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress

import uvicorn
from fastapi import FastAPI

from openresearch.api.app import create_app
from openresearch.config import Settings
from openresearch.distribution.local_sandbox import (
    LocalBackend,
    ManagedServer,
    local_backend,
    stop_server,
)
from openresearch.distribution.resources import web_root
from openresearch.sandbox.factory import build_sandbox
from openresearch.scheduler.worker import Worker


@contextmanager
def exit_signals(stopping: asyncio.Event) -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    original = {sig: signal.signal(sig, lambda *_: stopping.set()) for sig in signals}
    try:
        yield
    finally:
        for sig, handler in original.items():
            signal.signal(sig, handler)


def install_worker(app: FastAPI, failed: Callable[[BaseException], None]) -> None:
    api_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with api_lifespan(application):
            runtime = application.state.runtime
            worker = Worker(
                runtime.settings,
                runtime.repository,
                runtime.files,
                build_sandbox(runtime.settings),
            )
            future = asyncio.create_task(worker.run())

            def stopped(task: asyncio.Task[None]) -> None:
                if not worker.stopping.is_set():
                    error = asyncio.CancelledError() if task.cancelled() else task.exception()
                    failed(error or RuntimeError("local worker stopped unexpectedly"))

            future.add_done_callback(stopped)
            try:
                yield
            finally:
                # Worker 先取消活动执行并清理容器，API 再关闭共享数据库，最后停止本地沙箱。
                worker.stop()
                await future

    app.router.lifespan_context = lifespan


def stop_on_input_close(stopping: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def read() -> None:
        try:
            while sys.stdin.buffer.read(4096):
                pass
        finally:
            # 主循环已退出时无需再投递；守护线程不阻碍进程回收。
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(stopping.set)

    threading.Thread(target=read, name="application-input", daemon=True).start()


async def run_application(
    settings: Settings,
    stopping: asyncio.Event | None = None,
    *,
    exit_on_stdin_close: bool = False,
) -> None:
    stopping = stopping or asyncio.Event()
    if exit_on_stdin_close:
        stop_on_input_close(stopping)
    configuration = settings.model_copy(update={"web_dir": web_root(settings)})
    async with local_backend(configuration) as backend:
        await serve_application(backend, stopping)


async def serve_application(backend: LocalBackend, stopping: asyncio.Event) -> None:
    settings = backend.settings
    errors: list[BaseException] = []
    family = socket.AF_INET6 if ":" in settings.host else socket.AF_INET
    # API 也在创建后台任务前持有端口；绑定失败时可正常回收已启动的本地沙箱。
    with socket.create_server((settings.host, settings.port), family=family) as listener:
        app = create_app(backend.settings)
        server = ManagedServer(
            uvicorn.Config(
                app,
                host=settings.host,
                port=settings.port,
                lifespan="on",
                log_config=None,
                loop="asyncio",
                http="h11",
                timeout_graceful_shutdown=10,
            )
        )

        def failed(error: BaseException) -> None:
            errors.append(error)
            stopping.set()

        install_worker(app, failed)
        future = asyncio.create_task(server.serve(sockets=[listener]))
        stop = asyncio.create_task(stopping.wait())
        with exit_signals(stopping):
            try:
                watched = {future, stop}
                if backend.future is not None:
                    watched.add(backend.future)
                done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                if future in done and not server.started:
                    errors.append(RuntimeError("application API did not finish startup"))
                if backend.future in done:
                    errors.append(RuntimeError("local sandbox stopped unexpectedly"))
            finally:
                stop.cancel()
                await asyncio.gather(stop, return_exceptions=True)
                await stop_server(server, future)
        if errors:
            raise RuntimeError(
                "integrated application stopped after a component failure"
            ) from errors[0]
