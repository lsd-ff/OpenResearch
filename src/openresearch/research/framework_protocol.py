"""共同输入与来源协议；实验依赖和适配器只在沙箱内执行。"""

import io
import json
from importlib.resources import files

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.comparison import ComparisonProtocol, ComparisonSpec
from openresearch.models.context import Identity
from openresearch.models.research import BenchmarkSpec
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.research.benchmark import parse_dataset
from openresearch.services.uploads import UploadService
from openresearch.storage.files import ArtifactStore
from openresearch.storage.upload_files import UploadSubmission


def program(name: str) -> str:
    return files("openresearch.sandbox.programs").joinpath(name + ".txt").read_text("utf-8")


def read_protocol(plan: Plan, artifacts: ArtifactStore) -> ComparisonProtocol:
    return ComparisonProtocol.model_validate_json(
        artifacts.read(plan.artifacts["comparison_protocol"])
    )


def synthetic_cases() -> list[dict[str, object]]:
    documents = [
        {"id": "d1", "text": "The astronomy observatory telescope is called Vega."},
        {"id": "d2", "text": "The biology laboratory microscope is called Iris."},
        {"id": "d3", "text": "The geology expedition research vessel is called Atlas."},
        {"id": "d4", "text": "The chemistry laboratory spectrometer is called Nova."},
    ]
    questions = [
        ("What is the astronomy observatory telescope called?", "d1", "Vega"),
        ("What is the biology laboratory microscope called?", "d2", "Iris"),
        ("What is the geology expedition research vessel called?", "d3", "Atlas"),
        ("What is the chemistry laboratory spectrometer called?", "d4", "Nova"),
    ]
    return [
        {
            "id": str(index),
            "query": query,
            "documents": documents,
            "target": target,
            "answer": answer,
        }
        for index, (query, target, answer) in enumerate(questions)
    ]


def create_protocol(
    settings: Settings, uploads: UploadService, plan: Plan, task: Task
) -> TaskResult:
    if "comparison.protocol" not in task.contract.allowed_tools:
        raise ContractError("task is not authorized to create a comparison protocol")
    spec = ComparisonSpec.model_validate(task.inputs["comparison"])
    protocol = ComparisonProtocol(
        mode=spec.mode, origin="none", scope="基于真实发布来源的资料比较；未执行性能实验。"
    )
    if spec.mode == "benchmark":
        benchmark = spec.benchmark
        if benchmark is None:
            raw = json.dumps(synthetic_cases(), ensure_ascii=False).encode()
            record = uploads.create(
                UploadSubmission(
                    "comparison-synthetic.json",
                    io.BytesIO(raw),
                    Identity(plan.owner_id, plan.session_id),
                )
            )
            benchmark = BenchmarkSpec(
                upload_id=record.id,
                format="json",
                id_column="id",
                input_columns=["query", "documents"],
                target_column="target",
            )
        record, raw = uploads.content(benchmark.upload_id, plan.owner_id)
        dataset = parse_dataset(raw, benchmark, settings.benchmark_max_rows)
        protocol = ComparisonProtocol(
            mode="benchmark",
            origin="uploaded" if spec.benchmark else "synthetic",
            benchmark=benchmark,
            dataset_sha256=record.sha256,
            sample_count=len(dataset.inputs),
            answer_column=spec.answer_column if spec.benchmark else "answer",
            scope=(
                "真实框架检索，使用共同的 64 维 SHA-256 词嵌入。"
                + ("检索后调用相同的真实生成模型。" if spec.needs_answers else "本次不生成回答。")
                + "数据为明确构造的合成功能样例，不能视为真实业务或论文效果评测。"
                if spec.benchmark is None
                else "使用同一上传数据集，逐样本预测与指标由服务端校验。"
                "程序执行墙钟包含启动，不含依赖安装。"
            ),
        )
    return TaskResult(
        result=protocol.scope, outputs={"comparison_protocol": protocol.model_dump_json()}
    )
