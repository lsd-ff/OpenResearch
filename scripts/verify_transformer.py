"""通过真实 API/Worker/HTTP 沙箱执行 Transformer 核心复现，保存原始产物。"""

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from openresearch.config import Settings

COMMIT = "656470af9a5a13cb43975e148085a0aeadb87fda"
REPOSITORY = "https://github.com/AkiRusProd/numpy-transformer"


def response_json(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    return response.json()


def functional_rubric() -> dict[str, Any]:
    statements = [
        "Actual attention, positional encoding, encoder/decoder shape and causal assertions pass.",
        "Softmax, LogSoftmax and cross-entropy gradients agree with actual finite differences.",
        "Actual copy and reverse training each update encoder parameters and lower loss by 30%.",
    ]
    return {
        "paper_title": "Attention Is All You Need — user-requested CPU core checks",
        "claims": [
            {
                "title": statement[:190],
                "statement": statement,
                "criteria": [{"description": statement, "required_evidence": ["research_report"]}],
            }
            for statement in statements
        ],
    }


def create(
    client: httpx.Client, destination: Path, paper_file: Path | None, planning_mode: str
) -> dict[str, Any]:
    print(json.dumps({"stage": "load_original_paper"}), flush=True)
    if paper_file:
        raw = paper_file.read_bytes()
    else:
        pdf = client.get("/api/pdf-proxy", params={"url": "https://arxiv.org/abs/1706.03762"})
        pdf.raise_for_status()
        raw = pdf.content
    assert raw.startswith(b"%PDF"), "paper download must be an actual PDF"
    (destination / "attention-is-all-you-need.pdf").write_bytes(raw)
    upload = response_json(
        client.post(
            "/api/uploads",
            files={"file": ("attention-is-all-you-need.pdf", raw, "application/pdf")},
        )
    )
    code = (Path(__file__).resolve().parents[1] / "examples/transformer_cpu/verify.py").read_text(
        encoding="utf-8"
    )
    print(json.dumps({"stage": "create_plan", "paper_sha256": upload["sha256"]}), flush=True)
    return response_json(
        client.post(
            "/api/plan",
            json={
                "intent": "Transformer 论文核心复现实测：读取原论文，执行固定仓库组件的数值测试"
                "与 CPU copy/reverse 训练。"
                "使用提供的完整测试脚本；它包含上游梯度问题的复现和局部修正，所有结果来自实际执行。"
                "完成后增加 chat_agent 总结真实测试误差、损失和局限；不声称 WMT/BLEU 或泛化效果。",
                "workflow": "reproduce",
                "planning_mode": planning_mode,
                "attachments": [upload["id"]],
                "code": code,
                "research": {
                    "repository": {"url": REPOSITORY, "ref": COMMIT},
                    "entrypoint": "openresearch_run.py",
                    "patch_paths": ["openresearch_run.py"],
                    "max_repairs": 0,
                    "environment": {
                        "requirements": ["llvmlite==0.43.0", "numba==0.60.0", "numpy==2.0.2"]
                    },
                    "rubric": functional_rubric(),
                },
            },
        )
    )["plan_graph"]


def collect(client: httpx.Client, plan: dict[str, Any], destination: Path) -> dict[str, Any]:
    (destination / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for key, artifact in plan["artifacts"].items():
        response = client.get(f"/api/plans/{plan['id']}/artifacts/{artifact['id']}/content")
        response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        (destination / (key + ".txt")).write_bytes(response.content)
    events = client.get(f"/api/plans/{plan['id']}/events?after=0")
    events.raise_for_status()
    (destination / "events.json").write_bytes(events.content)
    report_file = destination / "research_report.txt"
    if not report_file.exists():
        return {}
    report = json.loads(report_file.read_bytes())
    attempt = report["runs"][0]["attempts"][-1]
    stdout = attempt["stdout"]
    print(stdout, flush=True)
    if attempt["stderr"]:
        print(attempt["stderr"], flush=True)
    results = [
        json.loads(line.removeprefix("TRANSFORMER_RESULT "))
        for line in stdout.splitlines()
        if line.startswith("TRANSFORMER_RESULT ")
    ]
    if report["success"]:
        assert report["success"] and len(results) == 1, "actual structured test results required"
        assert report["repository_commit"] == COMMIT
        for name in ("copy", "reverse"):
            assert 0 < results[0][name]["final_loss"] < 0.7 * results[0][name]["initial_loss"]
        (destination / "result.json").write_text(json.dumps(results[0], indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--user-id", default="transformer-verifier")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--plan-id", default=None)
    parser.add_argument("--paper-file", type=Path, default=None)
    parser.add_argument("--planning-mode", choices=["llm", "template"], default="llm")
    args = parser.parse_args()
    settings = Settings()
    session = args.session_id or "transformer-" + uuid4().hex
    destination = settings.data_dir / "transformer-acceptance" / session
    destination.mkdir(parents=True, exist_ok=True)
    headers = {"X-User-Id": args.user_id, "X-Session-Id": session}
    if settings.api_token.get_secret_value():
        headers["Authorization"] = "Bearer " + settings.api_token.get_secret_value()
    with httpx.Client(base_url=args.url, headers=headers, timeout=960, trust_env=False) as client:
        plan = (
            response_json(client.get(f"/api/plans/{args.plan_id}"))["plan_graph"]
            if args.plan_id
            else create(client, destination, args.paper_file, args.planning_mode)
        )
        print(json.dumps({"plan_id": plan["id"], "destination": str(destination)}), flush=True)
        if plan["status"] == "pending":
            response_json(client.post(f"/api/plans/{plan['id']}/execute"))
        deadline = time.monotonic() + settings.plan_timeout_seconds + 30
        previous = None
        while time.monotonic() < deadline:
            plan = response_json(client.get(f"/api/plans/{plan['id']}"))["plan_graph"]
            current = [(node["type"], node["status"], node["error"]) for node in plan["nodes"]]
            if current != previous:
                print(json.dumps(current, ensure_ascii=False), flush=True)
                previous = current
            if plan["status"] in {"completed", "failed", "canceled"} and not any(
                node["status"] == "in_progress" for node in plan["nodes"]
            ):
                collect(client, plan, destination)
                return 0 if plan["status"] == "completed" else 1
            time.sleep(1)
    raise TimeoutError("Transformer verification exceeded the plan deadline")


if __name__ == "__main__":
    raise SystemExit(main())
