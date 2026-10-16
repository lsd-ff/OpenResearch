"""验收独立 API/Worker 部署；示例代码仅作为请求传入沙箱，不在本机导入。"""

import argparse
import json
import time
from pathlib import Path

import httpx


def benchmark_request(upload_id: str) -> dict:
    code = (Path(__file__).parent.parent / "examples/openresearch_run.py").read_text(
        encoding="utf-8"
    )
    return {
        "intent": "验收部署：真实预测、消融与冻结规则",
        "workflow": "benchmark",
        "code": code,
        "research": {
            "benchmark": {
                "upload_id": upload_id,
                "input_columns": ["x"],
                "target_column": "y",
                "id_column": "id",
                "metrics": ["accuracy"],
            },
            "ablations": [{"name": "ablation", "arguments": ["--disable-parity"]}],
            "rubric": {
                "claims": [
                    {
                        "title": "基线准确率",
                        "statement": "示例基线 accuracy 至少为 1.0",
                        "criteria": [
                            {
                                "description": "完整预测重算",
                                "metric_name": "accuracy",
                                "expected_value": 1.0,
                                "operator": "at_least",
                            }
                        ],
                    }
                ]
            },
        },
    }


def require(response: httpx.Response) -> dict:
    response.raise_for_status()
    return response.json()


def wait_plan(client: httpx.Client, plan_id: str) -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        plan = require(client.get(f"/api/plans/{plan_id}"))["plan_graph"]
        if plan["status"] in {"completed", "failed", "canceled"}:
            assert plan["status"] == "completed", plan
            return plan
        time.sleep(0.25)
    raise TimeoutError("deployment experiment did not finish in 90 seconds")


def verify_single_node(client: httpx.Client) -> str:
    graph = require(client.post("/api/plan", json={"intent": "smoke"}))["plan_graph"]
    plan_id = graph["id"]
    first, second = graph["nodes"]
    require(
        client.post(
            f"/api/plans/{plan_id}/tasks/{first['id']}/reassign",
            json={"assigned_to": "research_coding_agent"},
        )
    )
    response = client.post("/api/execute", json={"task_id": first["id"]})
    response.raise_for_status()
    assert "event: result" in response.text
    current = require(client.get(f"/api/plans/{plan_id}"))["plan_graph"]
    assert current["status"] == "pending"
    assert current["nodes"][0]["status"] == "completed"
    assert current["nodes"][1]["status"] in {"pending", "ready"}
    response = client.post("/api/execute", json={"task_id": second["id"]})
    response.raise_for_status()
    assert "event: result" in response.text and "15" in response.text
    assert require(client.get(f"/api/plans/{plan_id}"))["plan_graph"]["status"] == "completed"
    return plan_id


def verify(base_url: str) -> None:
    headers = {"X-User-Id": "deployment-acceptance", "X-Session-Id": "deployment-acceptance"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=15) as client:
        assert client.get("/").status_code == 200
        assert require(client.get("/api/health"))["ok"]
        raw = (Path(__file__).parent.parent / "examples/benchmark.csv").read_bytes()
        upload = require(
            client.post("/api/uploads", files={"file": ("benchmark.csv", raw, "text/csv")})
        )
        response = require(client.post("/api/plan", json=benchmark_request(upload["id"])))
        plan_id = response["plan_graph"]["id"]
        require(client.post(f"/api/plans/{plan_id}/execute"))
        plan = wait_plan(client, plan_id)
        artifact = plan["artifacts"]["research_report"]
        report = require(client.get(f"/api/plans/{plan_id}/artifacts/{artifact['id']}/content"))
        assert report["success"]
        prediction_artifact = plan["artifacts"]["benchmark_predictions"]
        predictions = require(
            client.get(f"/api/plans/{plan_id}/artifacts/{prediction_artifact['id']}/content")
        )
        assert predictions
        assert [run["metrics"]["accuracy"] for run in report["runs"]] == [1.0, 0.5]
        assert report["runs"][0]["environment"]["container_image_id"].startswith("sha256:")
        events = require(client.get(f"/api/plans/{plan_id}/events"))
        assert events["events"]
        stream = client.get(f"/api/plans/{plan_id}/stream")
        stream.raise_for_status()
        assert "event: plan_event" in stream.text
        single_node_plan = verify_single_node(client)
        print(
            json.dumps(
                {
                    "plan_id": plan_id,
                    "status": plan["status"],
                    "scores": [1.0, 0.5],
                    "single_node_plan": single_node_plan,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    verify(parser.parse_args().url)
