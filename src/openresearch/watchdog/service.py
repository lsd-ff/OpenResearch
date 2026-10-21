import asyncio
import json
import time
from dataclasses import asdict
from uuid import uuid4

from openresearch.checkpoint.manager import CheckpointManager
from openresearch.checkpoint.registry import RollbackReceipt
from openresearch.errors import SandboxError
from openresearch.sandbox.core.models import (
    CommandResult,
    TerminalConnectionError,
    TerminalRecoveryError,
)
from openresearch.sandbox.core.session import CoreSandbox
from openresearch.watchdog.checker import Checker, has_crash_signal
from openresearch.watchdog.circuit import CircuitBreaker
from openresearch.watchdog.models import FailureKind, WatchdogConfig, WatchdogError


class Watchdog:
    def __init__(
        self,
        sandbox: CoreSandbox,
        checkpoints: CheckpointManager | None = None,
        config: WatchdogConfig | None = None,
        checker: Checker | None = None,
    ) -> None:
        if checkpoints is not None and checkpoints.sandbox is not sandbox:
            raise ValueError("watchdog checkpoints must belong to its sandbox")
        self.sandbox = sandbox
        self.checkpoints = checkpoints if checkpoints is not None else CheckpointManager(sandbox)
        self.config = config or WatchdogConfig()
        self.checker = checker or Checker(config=self.config.checker)
        self.breaker = CircuitBreaker(self.config.max_retry_window)
        self._lock = asyncio.Lock()

    async def execute(self, command: str, node_id: str = "command") -> CommandResult:
        if not node_id.strip() or len(node_id) > 200 or "\x00" in node_id:
            raise ValueError("invalid watchdog node id")
        async with self._lock:
            try:
                result = await self._execute(command, node_id)
            except asyncio.CancelledError:
                self._audit({"status": "canceled", "command": command, "node_id": node_id})
                raise
            except WatchdogError as exc:
                self._audit(
                    {
                        "status": "failed",
                        "command": command,
                        "node_id": node_id,
                        "kind": exc.kind.value,
                        "hint": exc.hint,
                        "result": self._result_data(exc.result),
                        "check": asdict(exc.report) if exc.report else None,
                    }
                )
                raise
            self._audit(
                {
                    "status": "completed",
                    "command": command,
                    "node_id": node_id,
                    "result": self._result_data(result),
                }
            )
            return result

    async def _execute(self, command: str, node_id: str) -> CommandResult:
        timeout = min(self.config.command_timeout_seconds, self.sandbox.config.timeout_seconds)
        try:
            result = await self.sandbox.execute(command, timeout_seconds=timeout)
        except TerminalRecoveryError as exc:
            result = exc.result
        except TerminalConnectionError as exc:
            self.checkpoints.record_failure_and_get_feedback(node_id, command, exc.result.output)
            raise WatchdogError(
                FailureKind.ENV_CORRUPTED,
                command,
                "终端连接已断开，需恢复检查点或关闭。",
                exc.result,
            ) from exc
        except SandboxError as exc:
            self.checkpoints.record_failure_and_get_feedback(node_id, command, type(exc).__name__)
            raise WatchdogError(
                FailureKind.ENV_CORRUPTED, command, "终端连接失败；检查回执并恢复已保存检查点。"
            ) from exc
        failed = not result.succeeded or has_crash_signal(result.output)
        if failed:
            self.checkpoints.record_failure_and_get_feedback(node_id, command, result.output)
        if result.timed_out:
            raise WatchdogError(
                FailureKind.TIMEOUT_HANG, command, "命令超时，检查交互等待或选择其他策略。", result
            )
        if result.output_limited:
            raise WatchdogError(
                FailureKind.OUTPUT_LIMIT, command, "实际输出超过预算，需减少程序输出。", result
            )
        if failed and self.breaker.record_and_check(command, result.output):
            raise WatchdogError(
                FailureKind.CIRCUIT_BROKEN, command, "相同命令和失败重复，必须调整策略。", result
            )
        report = await self.checker.audit(
            command, result, self.checkpoints.get_full_feedback_context()
        )
        if not report.succeeded:
            raise WatchdogError(FailureKind.ENV_CORRUPTED, command, report.hint, result, report)
        return result

    async def rollback_to_safe_state(self, image_id: str) -> RollbackReceipt:
        async with self._lock:
            receipt = await self.checkpoints.rollback(image_id)
            self._audit({"status": "restored", "rollback": asdict(receipt)})
            return receipt

    async def close(self) -> None:
        async with self._lock:
            await self.sandbox.close()

    @staticmethod
    def _result_data(result: CommandResult | None) -> dict[str, object] | None:
        if result is None:
            return None
        return {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "output_limited": result.output_limited,
            "audit_path": str(result.audit_path),
        }

    def _audit(self, event: dict[str, object]) -> None:
        root = self.sandbox.config.data_dir / "watchdog"
        root.mkdir(parents=True, exist_ok=True)
        data = {"schema": "openresearch.watchdog/v1", "created_at": time.time(), **event}
        (root / f"{uuid4().hex}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
