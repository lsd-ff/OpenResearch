"""显式启动的真实模型验收；生成和修复代码只由部署的 Worker 在 Docker 中执行。"""

import argparse
import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from openresearch.config import Settings

SOURCE_LINES = [
    "OpenResearch protocol acceptance fixture, not a published research paper.",
    "Predict the parity of x: prediction is int(x) modulo 2.",
    "Claim 1: Baseline accuracy on the four-row fixture is 1.0.",
    "Claim 2: The baseline execution prints MODEL_PARITY_CONFIRMED.",
]
RESEARCH_INTENT = (
    "这是软件协议验收，不是科学论文复现。请编写标准库 Python 程序："
    "从 OPENRESEARCH_INPUT 读取 [{id,inputs}]，将 int(inputs['x']) % 2 作为 prediction；"
    "写入 OPENRESEARCH_PREDICTIONS，输出 [{id,prediction}]，保留原始 ID。"
    "提供 --disable-parity 参数，指定时所有 prediction 为 0。"
    "正常执行时打印 MODEL_PARITY_CONFIRMED。不得获取目标标签或联网。"
    "只修改 openresearch_run.py；现有仓库是提供上下文的公共样本，不需要其依赖。"
)


def fixture_pdf(source_lines: list[str] | None = None) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    lines = "\n".join(f"({text}) Tj 0 -24 Td" for text in (source_lines or SOURCE_LINES))
    stream.set_data(f"BT /F1 11 Tf 36 740 Td {lines} ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class ModelAcceptance:
    def __init__(self, client: httpx.Client, output_dir: Path, plan_timeout: int) -> None:
        self.client = client
        self.output_dir = output_dir
        self.plan_timeout = plan_timeout
        self.results: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat()}

    def require(self, response: httpx.Response) -> dict[str, Any]:
        if response.is_error:
            raise RuntimeError(f"acceptance API request failed (HTTP {response.status_code})")
        return response.json()

    def save(self, name: str, value: dict[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def artifact(self, plan: dict[str, Any], key: str) -> dict[str, Any]:
        identifier = plan["artifacts"][key]["id"]
        data = self.require(
            self.client.get(f"/api/plans/{plan['id']}/artifacts/{identifier}/content")
        )
        self.save(f"{plan['id']}-{key}", data)
        return data

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        plan_id = self.require(self.client.post("/api/plan", json=request))["plan_graph"]["id"]
        self.results.setdefault("plan_ids", []).append(plan_id)
        self.save("progress", self.results)
        self.require(self.client.post(f"/api/plans/{plan_id}/execute"))
        deadline = time.monotonic() + self.plan_timeout + 10
        while time.monotonic() < deadline:
            plan = self.require(self.client.get(f"/api/plans/{plan_id}"))["plan_graph"]
            if plan["status"] == "completed":
                self.save(f"{plan_id}-plan", plan)
                return plan
            if plan["status"] in {"failed", "canceled", "blocked"}:
                self.save(f"{plan_id}-plan", plan)
                failed = [(node["type"], node["error"]) for node in plan["nodes"] if node["error"]]
                raise RuntimeError(f"model acceptance plan {plan_id} failed: {failed}")
            time.sleep(0.5)
        raise TimeoutError(f"model acceptance plan {plan_id} did not finish")

    def upload(self, name: str, raw: bytes, content_type: str) -> str:
        return self.require(
            self.client.post("/api/uploads", files={"file": (name, raw, content_type)})
        )["id"]

    def chat_and_documents(self) -> str:
        reply = self.require(
            self.client.post("/api/chat", json={"message": "请用中文回答：模型连接成功。"})
        )
        assert reply["response"] and not reply["citations"]
        self.save("chat", reply)
        upload_id = self.upload("model-fixture.pdf", fixture_pdf(), "application/pdf")
        plan = self.execute(
            {"intent": "解析验收 PDF", "workflow": "paper", "attachments": [upload_id]}
        )
        corpus = self.artifact(plan, "parsed_paper")
        assert SOURCE_LINES[2] in corpus["documents"][0]["pages"][0]["text"]
        reply = self.require(
            self.client.post(
                "/api/chat",
                json={"message": "请把 Claim 1 那句话翻译成中文，并附上原文逐字引用和页码。"},
            )
        )
        assert reply["citations"] and "1.0" in reply["response"]
        assert any(
            item["upload_id"] == upload_id and item["page"] == 1 for item in reply["citations"]
        )
        self.save("document-chat", reply)
        self.results["document_plan"] = plan["id"]
        print(
            "PASS: live chat, real PDF parsing, translation and validated page citation", flush=True
        )
        return upload_id

    def generated_code(self) -> None:
        plan = self.execute(
            {
                "intent": (
                    "生成标准库 Python 代码计算 sum(i*i for i in range(1,6))，"
                    "输出 MODEL_GENERATION_OK 和结果 55。"
                ),
                "workflow": "code",
            }
        )
        metrics = self.artifact(plan, "run_metrics")
        assert metrics["exit_code"] == 0
        assert "MODEL_GENERATION_OK" in metrics["stdout"] and "55" in metrics["stdout"]
        self.results["code_plan"] = plan["id"]
        print("PASS: model-generated code executed by the Docker Worker", flush=True)

    @staticmethod
    def benchmark_spec(upload_id: str) -> dict[str, Any]:
        return {
            "benchmark": {
                "upload_id": upload_id,
                "input_columns": ["x"],
                "target_column": "y",
                "id_column": "id",
                "metrics": ["accuracy"],
            },
            "ablations": [{"name": "ablation", "arguments": ["--disable-parity"]}],
            "max_repairs": 1,
        }

    def validate_experiment(self, plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        report = self.artifact(plan, "research_report")
        graph = self.artifact(plan, "claim_evidence_graph")
        self.artifact(plan, "final_patch")
        assert report["success"]
        assert [run["metrics"]["accuracy"] for run in report["runs"]] == [1.0, 0.5]
        assert all(
            run["environment"]["container_image_id"].startswith("sha256:") for run in report["runs"]
        )
        assert graph["claims"] and all(claim["status"] == "verified" for claim in graph["claims"])
        return report, graph

    def research(self, document_id: str, dataset_id: str) -> None:
        spec = self.benchmark_spec(dataset_id)
        spec["repository"] = {"url": "https://github.com/pallets/itsdangerous", "ref": "main"}
        plan = self.execute(
            {
                "intent": RESEARCH_INTENT,
                "workflow": "benchmark",
                "attachments": [document_id],
                "research": spec,
            }
        )
        report, graph = self.validate_experiment(plan)
        rubric = self.artifact(plan, "claim_rubric")
        assert any(claim["claim_type"] == "semantic" for claim in rubric["claims"])
        assert all(claim["source_sha256"] for claim in rubric["claims"])
        self.results["research_plan"] = plan["id"]
        self.results["research_scores"] = [run["metrics"]["accuracy"] for run in report["runs"]]
        self.results["verified_claims"] = len(graph["claims"])
        print(
            "PASS: live claim extraction, patch generation, baseline/ablation and evidence",
            flush=True,
        )

    def repair(self, dataset_id: str) -> None:
        plan = self.execute(
            {
                "intent": RESEARCH_INTENT,
                "workflow": "benchmark",
                "code": (
                    "raise RuntimeError('MODEL_REPAIR_REQUIRED: "
                    "implement the requested parity predictor')\n"
                ),
                "research": self.benchmark_spec(dataset_id),
            }
        )
        report = self.artifact(plan, "research_report")
        assert report["success"]
        attempts = report["runs"][0]["attempts"]
        assert (
            len(attempts) == 2 and attempts[0]["exit_code"] != 0 and attempts[1]["exit_code"] == 0
        )
        assert [run["metrics"]["accuracy"] for run in report["runs"]] == [1.0, 0.5]
        self.artifact(plan, "final_patch")
        self.results["repair_plan"] = plan["id"]
        self.results["repair_attempts"] = len(attempts)
        print(
            "PASS: intentional failure repaired by the live model and rerun in a fresh sandbox",
            flush=True,
        )

    def run(self) -> None:
        try:
            self._workflow()
            self.results["status"] = "passed"
        except Exception as exc:
            self.results["status"] = "failed"
            self.results["error"] = str(exc)[:2000]
            raise
        finally:
            self.results["finished_at"] = datetime.now(UTC).isoformat()
            self.save("result", self.results)
        print(json.dumps(self.results, ensure_ascii=False), flush=True)

    def _workflow(self) -> None:
        assert self.require(self.client.get("/api/health"))["ok"]
        print("RUN: chat and source citations", flush=True)
        document_id = self.chat_and_documents()
        print("RUN: generated code", flush=True)
        self.generated_code()
        dataset_id = self.upload(
            "benchmark.csv", b"id,x,y\na,0,0\nb,1,1\nc,2,0\nd,3,1\n", "text/csv"
        )
        print("RUN: claim extraction and research experiment", flush=True)
        self.research(document_id, dataset_id)
        print("RUN: live model repair", flush=True)
        self.repair(dataset_id)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calls the configured paid model and creates small Docker experiments."
    )
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    settings = Settings()
    identifier = "model-acceptance-" + uuid4().hex
    output_dir = args.output_dir or settings.data_dir / "model-acceptance" / identifier
    headers = {"X-User-Id": identifier, "X-Session-Id": identifier}
    if settings.api_token.get_secret_value():
        headers["Authorization"] = "Bearer " + settings.api_token.get_secret_value()
    with httpx.Client(
        base_url=args.url, headers=headers, timeout=settings.llm_timeout_seconds + 30
    ) as client:
        ModelAcceptance(client, output_dir, settings.plan_timeout_seconds).run()


if __name__ == "__main__":
    main()
