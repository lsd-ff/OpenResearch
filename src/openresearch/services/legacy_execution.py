import asyncio
import io
import re
from dataclasses import dataclass

from openresearch.config import Settings
from openresearch.errors import ContractError, NotFoundError
from openresearch.models.audit import AUDIT_ROLES
from openresearch.models.context import Identity
from openresearch.models.knowledge import KNOWLEDGE_ROLES
from openresearch.models.legacy import DirectExecutionRequest, LegacyEnvironmentState
from openresearch.models.runtime import LegacyTaskOrigin, Plan, Status
from openresearch.services.legacy_frameworks import (
    build_framework_stage,
    framework_spec,
    framework_stage,
    select_framework_stage,
)
from openresearch.services.legacy_gaps import build_gap_plan
from openresearch.services.legacy_inputs import text_input, uploaded_inputs
from openresearch.services.legacy_knowledge import build_knowledge_stage, select_knowledge_stage
from openresearch.services.legacy_plans import (
    CODE_KINDS,
    basic_legacy_plan,
    legacy_kind,
    validate_legacy_request,
)
from openresearch.services.legacy_reviews import build_review_plan
from openresearch.services.legacy_stages import (
    STAGES,
    build_stage_plan,
    select_stage,
    workflow_plan_id,
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
    operation: str | None = None


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
        reference = text_input(payload.inputs, "workflow_ref", limit=80)
        if reference:
            return await self._continuation(payload, identity, reference)
        existing = await self._existing(payload, identity)
        if existing:
            return existing
        if not payload.task_description.strip():
            raise ContractError("provide a saved task_id or a legacy task_description")
        kind = validate_legacy_request(payload)
        if kind in AUDIT_ROLES or kind == "fix_and_rerun":
            raise ContractError("result review requires an owned workflow_ref with saved evidence")
        payload = await self._paper(payload, identity)
        if kind in STAGES or framework_stage(kind) or kind in KNOWLEDGE_ROLES:
            return await self._new_stage(payload, identity, kind)
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
        if plan.legacy and plan.legacy.staged:
            if plan.status != Status.AWAITING_APPROVAL:
                await asyncio.to_thread(
                    self.repository.tasks.queue_stage, plan.id, payload.task_id, identity.user_id
                )
        elif plan.legacy and payload.task_id == plan.legacy.native_task_id:
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

    async def _new_stage(
        self, payload: DirectExecutionRequest, identity: Identity, kind: str
    ) -> DirectSelection:
        if framework_stage(kind):
            spec = await framework_spec(payload, self.plans)
            plan, target_id = await asyncio.to_thread(
                build_framework_stage, payload, identity, self.plans, kind, spec
            )
        elif kind in KNOWLEDGE_ROLES:
            plan, target_id = await asyncio.to_thread(
                build_knowledge_stage, payload, identity, self.plans, kind
            )
        else:
            plan, target_id = await asyncio.to_thread(
                build_stage_plan, payload, identity, self.plans, kind
            )
        plan.legacy = LegacyTaskOrigin(
            task_id=payload.task_id,
            task_type=kind,
            requested_type=payload.task_type,
            task_name=payload.task_name,
            assigned_to=payload.assigned_to,
            native_task_id=target_id,
            staged=True,
        )
        await asyncio.to_thread(self.repository.save_plan, plan)
        if not plan.approval.required:
            await asyncio.to_thread(
                self.repository.tasks.queue_stage, plan.id, target_id, identity.user_id
            )
        return DirectSelection(
            await asyncio.to_thread(self.repository.get_plan, plan.id), target_id, kind
        )

    async def _continuation(
        self, payload: DirectExecutionRequest, identity: Identity, reference: str
    ) -> DirectSelection:
        kind = validate_legacy_request(payload)
        if kind in AUDIT_ROLES or kind == "fix_and_rerun":
            if kind == "fix_and_rerun":
                plan = await asyncio.to_thread(
                    build_gap_plan, payload, identity, self.plans, self.files
                )
            else:
                plan = await asyncio.to_thread(
                    build_review_plan, payload, identity, self.plans, self.files, kind
                )
            await asyncio.to_thread(self.repository.save_plan, plan)
            if not plan.approval.required:
                await asyncio.to_thread(self.repository.queue_plan, plan.id, identity.user_id)
            return DirectSelection(
                await asyncio.to_thread(self.repository.get_plan, plan.id), plan.nodes[0].id, kind
            )
        framework = framework_stage(kind)
        if kind not in STAGES and framework is None and kind not in KNOWLEDGE_ROLES:
            raise ContractError("this operation does not select a managed scientific stage")
        if payload.plan_id or set(payload.inputs) - {"workflow_ref", "stage_task_id"}:
            raise ContractError(
                "continuation only selects a saved stage; new inputs cannot replace its contract"
            )
        plan = await asyncio.to_thread(
            self.repository.get_plan, workflow_plan_id(reference), identity.user_id
        )
        selector = (
            select_framework_stage
            if framework
            else select_knowledge_stage
            if kind in KNOWLEDGE_ROLES
            else select_stage
        )
        target = selector(plan, payload, kind)
        if plan.status != Status.AWAITING_APPROVAL:
            await asyncio.to_thread(
                self.repository.tasks.queue_stage, plan.id, target.id, identity.user_id
            )
        return DirectSelection(
            await asyncio.to_thread(self.repository.get_plan, plan.id), target.id, kind
        )

    async def _paper(
        self, payload: DirectExecutionRequest, identity: Identity
    ) -> DirectExecutionRequest:
        if legacy_kind(payload) not in STAGES.keys() | KNOWLEDGE_ROLES.keys() | {
            "paper_parse"
        } or uploaded_inputs(payload):
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
