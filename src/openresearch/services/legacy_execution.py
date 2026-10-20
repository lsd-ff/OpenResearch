import asyncio
import io
import re
from dataclasses import dataclass

from openresearch.config import Settings
from openresearch.errors import ContractError, NotFoundError
from openresearch.models.context import Identity
from openresearch.models.legacy import DirectExecutionRequest, LegacyEnvironmentState
from openresearch.models.runtime import Plan, Status
from openresearch.services.legacy_inputs import text_input, uploaded_inputs
from openresearch.services.legacy_plans import (
    CODE_KINDS,
    basic_legacy_plan,
    legacy_kind,
    validate_legacy_request,
)
from openresearch.services.plans import PlanService
from openresearch.services.uploads import UploadService
from openresearch.sources.remote_pdf import RemotePdfReader, pdf_filename
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository
from openresearch.storage.upload_files import UploadSubmission


@dataclass(frozen=True)
class DirectSelection:
    plan: Plan
    task_id: str


class LegacyExecutionService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        plans: PlanService,
        uploads: UploadService,
        files: ArtifactStore,
    ) -> None:
        self.settings, self.repository, self.plans, self.uploads = (
            settings,
            repository,
            plans,
            uploads,
        )
        self.files = files

    async def prepare(self, payload: DirectExecutionRequest, identity: Identity) -> DirectSelection:
        existing = await self._existing(payload, identity)
        if existing:
            return existing
        if not payload.task_description.strip():
            raise ContractError("provide a saved task_id or a legacy task_description")
        validate_legacy_request(payload)
        payload = await self._paper(payload, identity)
        state = await self._environment(payload, identity)
        plan = await asyncio.to_thread(basic_legacy_plan, payload, identity, self.plans, state)
        await asyncio.to_thread(self.repository.save_plan, plan)
        if not plan.approval.required:
            await asyncio.to_thread(self.repository.queue_plan, plan.id, identity.user_id)
        return DirectSelection(
            await asyncio.to_thread(self.repository.get_plan, plan.id), plan.nodes[-1].id
        )

    async def _existing(
        self, payload: DirectExecutionRequest, identity: Identity
    ) -> DirectSelection | None:
        plan_id = payload.plan_id
        if plan_id is None and payload.task_id:
            try:
                plan_id = await asyncio.to_thread(
                    self.repository.tasks.find_plan, payload.task_id, identity.user_id
                )
            except NotFoundError:
                if not payload.task_description.strip():
                    raise
        if plan_id is None:
            return None
        plan = await asyncio.to_thread(self.repository.get_plan, plan_id, identity.user_id)
        if not any(node.id == payload.task_id for node in plan.nodes):
            raise NotFoundError("task not found")
        if plan.legacy and payload.task_id == plan.legacy.native_task_id:
            # 已保存的旧请求可在审批后恢复或断线重读；绝不以新输入覆盖冻结契约。
            if plan.status == Status.PENDING:
                await asyncio.to_thread(self.repository.queue_plan, plan.id, identity.user_id)
        else:
            await asyncio.to_thread(
                self.repository.tasks.queue, plan.id, payload.task_id, identity.user_id
            )
        return DirectSelection(
            await asyncio.to_thread(self.repository.get_plan, plan.id), payload.task_id
        )

    async def _paper(
        self, payload: DirectExecutionRequest, identity: Identity
    ) -> DirectExecutionRequest:
        if legacy_kind(payload) != "paper_parse" or uploaded_inputs(payload):
            return payload
        url = text_input(payload.inputs, "pdf_url", "paper_url", limit=4096)
        if url is None:
            return payload
        raw = await RemotePdfReader(self.settings).fetch(url)
        record = await asyncio.to_thread(
            self.uploads.create, UploadSubmission(pdf_filename(url), io.BytesIO(raw), identity)
        )
        return payload.model_copy(update={"inputs": {**payload.inputs, "attachments": [record.id]}})

    async def _environment(
        self, payload: DirectExecutionRequest, identity: Identity
    ) -> LegacyEnvironmentState | None:
        if legacy_kind(payload) not in CODE_KINDS | {
            "resolve_dependencies",
            "prepare_runtime",
            "install_dependencies",
        }:
            return None
        reference = text_input(
            payload.inputs, "runtime_session", "runtime_env", "prepared_runtime", limit=80
        )
        if not reference:
            return None
        match = re.fullmatch(r"openresearch:([a-f0-9]{32})", reference)
        if not match:
            raise ContractError(
                "prepare an owned runtime configuration; raw sandbox IDs cannot be imported"
            )
        plan = await asyncio.to_thread(self.repository.get_plan, match[1], identity.user_id)
        if plan.status != Status.COMPLETED or "legacy_environment_state" not in plan.artifacts:
            raise ContractError("prepared runtime has no completed environment receipt")
        return LegacyEnvironmentState.model_validate_json(
            await asyncio.to_thread(self.files.read, plan.artifacts["legacy_environment_state"])
        )
