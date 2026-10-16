"""显式付费验收聊天自动规划；仅通过消息与附件启动，源码只在 Docker 中运行。"""

import argparse
import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from verify_model import ModelAcceptance, fixture_pdf

from openresearch.config import Settings

REPOSITORY = "https://github.com/pallets/itsdangerous"
GOAL = (
    f"请用 {REPOSITORY} 的真实源码，在上传数据上跑一个 CPU benchmark。"
    "这只是签名协议功能验收，不是科学论文指标复现。"
    "每行先用 URLSafeSerializer 和固定测试密钥生成 token；tampered 为 1 时，"
    "替换签名的第一个字符，确保改变签名字节。使用 loads 校验：有效预测 yes，"
    "BadSignature 预测 no。必须调用仓库原实现，不能直接根据输入映射预测。"
    "另做关闭签名验证的消融，始终接受 token 并预测 yes。"
    "打印 AUTO_SOURCE_CONFIRMED 和 itsdangerous.__file__，证明导入的是仓库 src 源码。"
)
PAPER_LINES = [
    "OpenResearch automatic workflow protocol fixture, not a scientific paper.",
    f"Repository: {REPOSITORY}",
    "Claim 1: The signed object survives a dumps and loads round trip.",
    "Claim 2: The adapter prints AUTO_ROUNDTRIP_CONFIRMED after the round trip.",
]


class AutoAcceptance(ModelAcceptance):
    def message(self, message: str, attachments: list[str] | None = None) -> dict[str, Any]:
        return self.require(
            self.client.post(
                "/api/messages", json={"message": message, "attachments": attachments or []}
            )
        )

    def run_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["type"] == "plan", "natural request must create a plan"
        plan_id = payload["plan_graph"]["id"]
        self.results.setdefault("plan_ids", []).append(plan_id)
        self.save("progress", self.results)
        self.require(self.client.post(f"/api/plans/{plan_id}/execute"))
        deadline = time.monotonic() + self.plan_timeout + 10
        while time.monotonic() < deadline:
            graph = self.require(self.client.get(f"/api/plans/{plan_id}"))["plan_graph"]
            if graph["status"] in {"completed", "failed", "canceled"}:
                self.save(plan_id, graph)
                assert graph["status"] == "completed", [
                    node.get("error") for node in graph["nodes"] if node.get("error")
                ]
                return graph
            time.sleep(1)
        raise TimeoutError("automatic experiment exceeded its configured budget")

    def benchmark(self) -> None:
        dataset = self.upload(
            "signature.csv",
            b"id,tampered,verdict\na,0,yes\nb,1,no\nc,0,yes\nd,1,no\n",
            "text/csv",
        )
        question = self.message(GOAL, [dataset])
        self.save("benchmark-question", question)
        assert question["type"] == "clarification" and "verdict" in question["response"]
        # 重新读取持久化会话后仅发送短答，不重新传附件或 ResearchSpec。
        context = self.require(self.client.get("/api/session"))
        assert context["pending_planning"]["attachments"] == [dataset]
        answer = self.message("输入列是 tampered，标签列是 verdict，id 是 ID 列。")
        self.save("benchmark-answer", answer)
        plan = self.run_message(answer)
        spec = self.artifact(plan, "research_spec")
        report = self.artifact(plan, "research_report")
        self.artifact(plan, "experiment_design")
        self.artifact(plan, "final_patch")
        self.artifact(plan, "repository_snapshot")
        assert spec["benchmark"]["target_column"] == "verdict"
        assert spec["benchmark"]["input_columns"] == ["tampered"]
        assert report["success"] and report["repository_commit"]
        assert [run["metrics"]["accuracy"] for run in report["runs"]] == [1.0, 0.5]
        for run in report["runs"]:
            stdout = run["attempts"][-1]["stdout"]
            assert "AUTO_SOURCE_CONFIRMED" in stdout
            assert "/repository/src/itsdangerous/__init__.py" in stdout
            assert run["environment"]["container_image_id"].startswith("sha256:")
        self.results["benchmark_plan"] = plan["id"]
        self.results["scores"] = [run["metrics"]["accuracy"] for run in report["runs"]]
        print(
            "PASS: natural benchmark, durable clarification, source import and real ablation",
            flush=True,
        )

    def paper_workflows(self) -> None:
        document = self.upload(
            "automatic-protocol.pdf", fixture_pdf(PAPER_LINES), "application/pdf"
        )
        plan = self.run_message(
            self.message("请分析这篇附件，总结两条验收主张，并附原文逐字引用及页码。", [document])
        )
        reply = self.artifact(plan, "paper_analysis")
        assert reply["citations"] and all(item["page"] == 1 for item in reply["citations"])
        self.results["paper_analysis_plan"] = plan["id"]
        # 不在消息中提供仓库地址，验证从原文提取仓库并自动设计入口。
        graph = self.run_message(
            self.message(
                "请复现这篇附件中的协议验收：使用原仓库 URLSafeSerializer，"
                "对一个真实对象执行 dumps 后 loads，断言 round trip 得到原对象。"
                "成功后打印 AUTO_ROUNDTRIP_CONFIRMED，并打印 itsdangerous.__file__。"
                "这是 CPU 功能验收，不是科学论文复现。",
                [document],
            )
        )
        snapshot = self.artifact(graph, "repository_snapshot")
        assert snapshot["url"] == REPOSITORY
        report = self.artifact(graph, "research_report")
        assert (
            report["success"]
            and "AUTO_ROUNDTRIP_CONFIRMED" in report["runs"][0]["attempts"][-1]["stdout"]
        )
        self.artifact(graph, "claim_evidence_graph")
        self.results["paper_reproduction_plan"] = graph["id"]
        print(
            "PASS: natural PDF analysis with citations and repository discovery from source",
            flush=True,
        )

    def run(self) -> None:
        try:
            self.benchmark()
            self.paper_workflows()
            self.results["status"] = "passed"
        except Exception as exc:
            self.results["status"] = "failed"
            self.results["error"] = str(exc)[:2000]
            raise
        finally:
            self.results["finished_at"] = datetime.now(UTC).isoformat()
            self.save("result", self.results)
        print(json.dumps(self.results, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Uses the configured paid model and Docker Worker."
    )
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    settings = Settings()
    identifier = "auto-acceptance-" + uuid4().hex
    output_dir = settings.data_dir / "auto-acceptance" / identifier
    headers = {"X-User-Id": identifier, "X-Session-Id": identifier}
    if settings.api_token.get_secret_value():
        headers["Authorization"] = "Bearer " + settings.api_token.get_secret_value()
    with httpx.Client(
        base_url=args.url, headers=headers, timeout=settings.llm_timeout_seconds * 3 + 150
    ) as client:
        AutoAcceptance(client, output_dir, settings.plan_timeout_seconds).run()


if __name__ == "__main__":
    main()
