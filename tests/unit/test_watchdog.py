import asyncio
import json
from pathlib import Path

import pytest

from openresearch.checkpoint.feedback import FeedbackHistory
from openresearch.checkpoint.registry import RollbackReceipt
from openresearch.errors import ModelError
from openresearch.sandbox.core.models import CommandResult
from openresearch.watchdog.checker import Checker
from openresearch.watchdog.circuit import CircuitBreaker
from openresearch.watchdog.models import CheckerConfig


def result(output: str, code: int = 1) -> CommandResult:
    return CommandResult(output, code, False, False, output, Path("unused-test-audit"))


def test_circuit_uses_failure_window_and_unambiguous_command_output_hashes():
    breaker = CircuitBreaker(3)
    assert not breaker.record_and_check("ab", "c")
    assert not breaker.record_and_check("a", "bc")
    assert not breaker.record_and_check("ab", "c")
    assert not breaker.record_and_check("ab", "c")
    assert breaker.record_and_check("ab", "c")
    breaker.reset()
    assert not breaker.record_and_check("ab", "c")
    with pytest.raises(ValueError):
        CircuitBreaker(0)


def test_feedback_is_bounded_unicode_safe_and_only_records_actual_rollback_facts():
    history = FeedbackHistory(max_records=3, max_context_chars=3000)
    assert history.context() == ""
    for index in range(5):
        history.record_failure(str(index), "命令" * 1000, "错误" * 1000)
    assert history.failure_count == 5 and len(history.failures()) == 3
    context = json.loads(history.context())
    assert len(history.context()) <= 3000 and context["omitted_failures"] >= 2
    assert context["last_rollback"] is None
    receipt = RollbackReceipt("old", "new", "safe", "sha256:safe")
    history.record_restoration(receipt)
    assert json.loads(history.context())["last_rollback"]["node_id"] == "safe"
    history.record_failure("later", "different", "still fails")
    assert json.loads(history.context())["last_rollback"] is None
    history.reset()
    assert history.failure_count == 0 and history.context() == ""


@pytest.mark.parametrize("with_restore", [False, True])
def test_feedback_context_budget_counts_json_escaping(with_restore: bool):
    history = FeedbackHistory(max_context_chars=2000)
    latest = history.record_failure("\x03" * 200, "\x01" * 2000, "\x02" * 2000)
    if with_restore:
        history.record_restoration(RollbackReceipt("old", "new", "\x04" * 200, "\x05" * 200))
    encoded = history.context()
    assert len(encoded) <= 2000
    assert json.loads(encoded)["failures"][0]["output_sha256"] == latest.output_sha256


@pytest.mark.parametrize(
    "output", ["", "error: old cache; installation finished", "failed to use cache"]
)
async def test_checker_trusts_zero_exit_without_calling_advisor(output: str):
    report = await Checker().audit("command", result(output, 0))
    assert report.succeeded and report.advice_attempts == 0


async def test_checker_preserves_crash_signal_even_if_shell_reports_zero():
    report = await Checker().audit("command", result("Fatal Python error: lost runtime", 0))
    assert not report.succeeded and "crash" in report.hint.lower()


@pytest.mark.parametrize("is_timeout", [True, False])
async def test_checker_never_promotes_timed_out_or_limited_zero_exit_to_success(is_timeout: bool):
    bounded = CommandResult(
        "partial", 0, is_timeout, not is_timeout, "partial", Path("unused-audit")
    )
    report = await Checker().audit("command", bounded)
    assert not report.succeeded and report.advice_attempts == 0


async def test_advice_retries_are_bounded_and_never_change_actual_failure_to_success():
    class Advisor:
        calls = 0

        async def suggest(self, diagnostic):
            self.calls += 1
            assert len(diagnostic.output) <= 2000
            assert diagnostic.feedback == "prior evidence"
            if self.calls < 3:
                raise ModelError("unavailable")
            return "准备缺失的依赖，并保留实际失败记录。"

    advisor = Advisor()
    report = await Checker(advisor).audit("missing", result("error" * 5000), "prior evidence")
    assert not report.succeeded and advisor.calls == report.advice_attempts == 3
    assert report.advice_errors == ("ModelError", "ModelError")
    assert "依赖" in report.hint


async def test_advice_timeouts_fall_back_without_retrying_the_command():
    class HangingAdvisor:
        calls = 0

        async def suggest(self, diagnostic):
            self.calls += 1
            await asyncio.sleep(60)
            return "never returned"

    advisor = HangingAdvisor()
    checker = Checker(advisor, CheckerConfig(max_advice_attempts=2, advice_timeout_seconds=0.01))
    report = await checker.audit("missing", result("command not found"))
    assert not report.succeeded and report.advice_attempts == advisor.calls == 2
    assert report.advice_errors == ("TimeoutError", "TimeoutError")


async def test_checker_cancellation_is_not_converted_to_advice_failure():
    class CanceledAdvisor:
        async def suggest(self, diagnostic):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await Checker(CanceledAdvisor()).audit("command", result("real failure"))
