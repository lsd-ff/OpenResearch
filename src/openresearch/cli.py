import argparse
import asyncio
import json
import signal
from contextlib import suppress

import uvicorn

from openresearch.api.app import create_app
from openresearch.config import Settings
from openresearch.logging import configure_logging
from openresearch.sandbox.factory import build_sandbox
from openresearch.scheduler.worker import Worker
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


async def run_worker(settings: Settings) -> None:
    repository = Repository(settings.database_url)
    files = ArtifactStore(settings.data_dir / "artifacts", settings.max_output_bytes * 3)
    worker = Worker(settings, repository, files, build_sandbox(settings))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.stop)
        # Windows 不支持事件循环信号句柄，asyncio.run 仍处理 Ctrl+C 并触发清理。
    try:
        await worker.run()
    finally:
        repository.close()


async def doctor(settings: Settings) -> bool:
    sandbox = build_sandbox(settings)
    try:
        ok = await sandbox.health()
        print(
            json.dumps(
                {
                    "python_runtime": "ok",
                    "data_dir": str(settings.data_dir),
                    "sandbox": settings.sandbox_backend,
                    "sandbox_ok": ok,
                }
            )
        )
        return ok
    finally:
        await sandbox.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenResearch Python runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help="start the HTTP API")
    subparsers.add_parser("worker", help="start the durable execution worker")
    subparsers.add_parser("sandbox", help="start the isolated execution HTTP service")
    subparsers.add_parser("doctor", help="check the configured execution backend")
    db = subparsers.add_parser("db", help="database maintenance")
    db.add_argument("action", choices=["upgrade"])
    args = parser.parse_args()
    configure_logging()
    settings = Settings()
    if args.command == "serve":
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    elif args.command == "worker":
        asyncio.run(run_worker(settings))
    elif args.command == "sandbox":
        from openresearch.sandbox.service import create_sandbox_app

        uvicorn.run(
            create_sandbox_app(settings), host=settings.host, port=settings.sandbox_service_port
        )
    elif args.command == "doctor":
        raise SystemExit(0 if asyncio.run(doctor(settings)) else 1)
    elif args.command == "db":
        repository = Repository(settings.database_url)
        repository.close()
        print("database upgraded")


if __name__ == "__main__":
    main()
