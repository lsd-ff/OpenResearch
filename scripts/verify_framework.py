"""记录真实模型 DAG、HTTP 沙箱、数据 Agent 与 SSE 的部署回执；不证明科研效果。"""

import argparse
import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from openresearch.config import Settings


def expect(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def create_plan(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/plan", json=payload)
    response.raise_for_status()
    return response.json()["plan_graph"]  # type: ignore[no-any-return]


def run_plan(client: httpx.Client, plan: dict[str, Any]) -> dict[str, Any]:
    queued = client.post(f"/api/plans/{plan['id']}/execute")
    expect(queued.status_code == 202, "plan_was_not_queued")
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        response = client.get(f"/api/plans/{plan['id']}")
        response.raise_for_status()
        current = response.json()["plan_graph"]
        if current["status"] in {"completed", "failed", "canceled"}:
            expect(current["status"] == "completed", "plan_did_not_complete")
            return current  # type: ignore[no-any-return]
        time.sleep(0.25)
    raise RuntimeError("plan_deadline_exceeded")


def artifact(client: httpx.Client, plan: dict[str, Any], key: str) -> str:
    identifier = plan["artifacts"][key]["id"]
    response = client.get(f"/api/plans/{plan['id']}/artifacts/{identifier}/content")
    response.raise_for_status()
    return response.text


def verify_model_dag(client: httpx.Client) -> dict[str, Any]:
    plan = create_plan(
        client,
        {
            "intent": "运行给定 Python 代码，完成后由 chat_agent 总结真实输出；无需额外检索。",
            "workflow": "code",
            "code": "print(6 * 7)",
            "planning_mode": "llm",
        },
    )
    expect(plan["planning"]["strategy"] == "llm", "planner_used_template_instead_of_real_model")
    expect(
        any(node["assigned_to"] == "chat_agent" for node in plan["nodes"]), "missing_chat_summary"
    )
    finished = run_plan(client, plan)
    metrics = json.loads(artifact(client, finished, "run_metrics"))
    expect(
        metrics["exit_code"] == 0 and metrics["stdout"].strip() == "42", "unexpected_actual_output"
    )
    response = client.get(f"/api/plans/{plan['id']}/stream")
    response.raise_for_status()
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    expect(events[-1]["event_type"] == "plan_completed", "missing_durable_terminal_sse")
    cursor = events[-1]["sequence"]
    resumed = client.get(f"/api/plans/{plan['id']}/stream", headers={"Last-Event-ID": str(cursor)})
    expect(resumed.status_code == 200 and not resumed.text.strip(), "sse_replayed_consumed_events")
    return {
        "name": "real_model_dag_http_sandbox_sse",
        "plan_id": plan["id"],
        "strategy": plan["planning"]["strategy"],
        "actual_stdout": metrics["stdout"],
        "roles": [node["assigned_to"] for node in finished["nodes"]],
        "artifact_keys": list(finished["artifacts"]),
        "events": len(events),
    }


def verify_data_agent(client: httpx.Client) -> dict[str, Any]:
    response = client.post(
        "/api/uploads", files={"file": ("measurements.csv", b"x,label\n1,a\n3,b\n,a\n")}
    )
    response.raise_for_status()
    upload = response.json()
    plan = create_plan(
        client,
        {
            "intent": "分析实际上传数据的统计值和缺失值",
            "workflow": "data",
            "attachments": [upload["id"]],
            "planning_mode": "template",
        },
    )
    finished = run_plan(client, plan)
    profile = json.loads(artifact(client, finished, "data_profile"))
    expect(
        profile["row_count"] == 3 and profile["columns"]["x"]["mean"] == 2,
        "incorrect_data_statistics",
    )
    expect(profile["source"]["sha256"] == upload["sha256"], "missing_data_provenance")
    expect("1/3" in artifact(client, finished, "data_chart"), "missing_actual_data_chart")
    return {"name": "actual_data_agent", "plan_id": plan["id"], "row_count": 3, "actual_mean": 2}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    settings = Settings()
    run_id = uuid4().hex
    destination = settings.data_dir / "framework-acceptance" / (run_id + ".json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, Any] = {
        "scope": "real_model_dag_http_sandbox_data_and_sse",
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "stages": [],
    }
    headers = {"X-User-Id": "framework-verifier", "X-Session-Id": run_id}
    if settings.api_token.get_secret_value():
        headers["Authorization"] = "Bearer " + settings.api_token.get_secret_value()
    try:
        with httpx.Client(
            base_url=args.url, headers=headers, timeout=960, trust_env=False
        ) as client:
            for verify in (verify_model_dag, verify_data_agent):
                result = verify(client)
                receipt["stages"].append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                destination.write_text(
                    json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        receipt["status"] = "passed"
        return 0
    except Exception as exc:
        receipt.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, RuntimeError):
            receipt["reason"] = str(exc)
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), flush=True)
        return 1
    finally:
        receipt["finished_at"] = datetime.now(UTC).isoformat()
        destination.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(destination), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
