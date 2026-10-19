import asyncio
import base64
import json

from openresearch.agents.claim_freeze import ClaimFreezer
from openresearch.agents.context import AgentContext
from openresearch.agents.librarian import LibrarianAgent
from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.runtime import TaskResult
from openresearch.research.data_profile import missing_values_chart, profile_records, read_records
from openresearch.services.uploads import UploadService
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


class DataAgent:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        files: ArtifactStore,
        librarian: LibrarianAgent,
    ) -> None:
        self.settings = settings
        self.uploads = UploadService(settings, repository)
        self.claims = ClaimFreezer(settings, files)
        self.librarian = librarian

    async def execute(self, context: AgentContext) -> TaskResult:
        plan, task = context.plan, context.task
        if task.type == "claim_freeze":
            return await self.claims.execute(plan, task)
        if task.type in {"document_extract", "paper_analyze"}:
            return await self.librarian.execute(context)
        if task.type != "data_analyze" or "data.profile" not in task.contract.allowed_tools:
            raise ContractError("unsupported data agent task")
        identifier = task.inputs.get("upload_id")
        if not isinstance(identifier, str) or identifier not in plan.attachments:
            raise ContractError("data task must use a declared uploaded dataset")
        record, raw = await asyncio.to_thread(self.uploads.content, identifier, plan.owner_id)
        records = await asyncio.to_thread(
            read_records, raw, record.name, self.settings.benchmark_max_rows
        )
        profile = await asyncio.to_thread(profile_records, records)
        profile["source"] = {"upload_id": record.id, "name": record.name, "sha256": record.sha256}
        payload = json.dumps(profile, ensure_ascii=False, allow_nan=False)
        chart = missing_values_chart(profile)
        return TaskResult(
            result=(
                f"分析了 {len(records)} 行、{len(profile['columns'])} 列；统计值来自实际上传数据。"
            ),
            structured_data=payload,
            image_base64="data:image/svg+xml;base64," + base64.b64encode(chart.encode()).decode(),
            outputs={"data_profile": payload, "data_chart": chart},
        )
