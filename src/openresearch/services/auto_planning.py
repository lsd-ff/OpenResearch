import asyncio
import re
from dataclasses import replace

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.comparison import ComparisonSpec, FrameworkTarget
from openresearch.models.context import Conversation
from openresearch.models.planning import IntentDraft, PendingPlanning
from openresearch.models.research import BenchmarkSpec, ResearchSpec
from openresearch.models.submissions import PlanSubmission
from openresearch.services.intent import IntentParser, requests_source_reproduction
from openresearch.services.planning_inputs import (
    benchmark_mapping,
    dataset_profiles,
    repository_urls,
)
from openresearch.services.planning_outcomes import PlanningOutcome, planning_question
from openresearch.services.planning_sources import PlanningSources
from openresearch.services.plans import PlanService
from openresearch.services.uploads import UploadService
from openresearch.sources.discovery import SourceDiscovery


class AutoPlanner:
    def __init__(
        self,
        settings: Settings,
        plans: PlanService,
        uploads: UploadService,
        discovery: SourceDiscovery,
    ) -> None:
        self.settings = settings
        self.plans = plans
        self.uploads = uploads
        self.parser = IntentParser(settings)
        self.sources = PlanningSources(settings, uploads, discovery, self.parser.llm)

    async def resolve(self, submission: PlanSubmission, snapshot: Conversation) -> PlanningOutcome:
        identifiers = list(submission.attachments)
        pending = snapshot.pending_planning
        if pending:
            identifiers = list(dict.fromkeys([*pending.attachments, *identifiers]))
        records = [
            self.uploads.repository.uploads.get(key, snapshot.owner_id) for key in identifiers
        ]
        draft = await self.parser.parse(
            snapshot,
            submission.intent,
            [
                {"id": record.id, "name": record.name, "content_type": record.content_type}
                for record in records
            ],
        )
        if draft.workflow == "chat":
            return PlanningOutcome(snapshot, "", pending=pending, chat=True)
        if pending and not draft.continue_pending:
            pending = None
            identifiers = list(submission.attachments)
        goal = "\n".join([pending.intent, submission.intent]) if pending else submission.intent
        if len(goal) > 16000 or len(identifiers) > 8:
            raise ContractError("planning context exceeds limit; start a new conversation")
        if not identifiers and re.search(
            r"这篇|这个|上传|附件|刚才|attached|uploaded", goal, re.IGNORECASE
        ):
            identifiers = list(snapshot.uploads)
        state = PendingPlanning(
            intent=goal,
            attachments=identifiers,
            repository_candidates=pending.repository_candidates if pending else [],
            paper_candidates=pending.paper_candidates if pending else [],
        )
        if draft.workflow == "framework":
            return await self._framework(submission, snapshot, state, draft)
        if draft.workflow == "data":
            return await self._data(submission, snapshot, state, draft)
        if draft.workflow == "code" and requests_source_reproduction(goal):
            draft.workflow = "reproduce"
        if draft.workflow == "code":
            code = submission.code or self._uploaded_code(state, snapshot.owner_id)
            plan = await asyncio.to_thread(
                self.plans.build,
                replace(
                    submission, intent=goal, workflow="code", attachments=identifiers, code=code
                ),
            )
            return PlanningOutcome(snapshot, "代码执行计划已生成，可在右侧运行。", plan=plan)
        return await self._materials(submission, snapshot, state, draft)

    async def _framework(
        self,
        submission: PlanSubmission,
        snapshot: Conversation,
        state: PendingPlanning,
        draft: IntentDraft,
    ) -> PlanningOutcome:
        benchmark = None
        if draft.framework_mode == "benchmark":
            profiles = await asyncio.to_thread(
                dataset_profiles,
                self.uploads,
                state.attachments,
                snapshot.owner_id,
                self.settings.benchmark_max_rows,
            )
            if profiles:
                benchmark, question = await self._benchmark(
                    snapshot, state, draft.model_copy(update={"workflow": "benchmark"})
                )
                if question:
                    return planning_question(snapshot, state, question)
        answer_column = None
        if benchmark and {"query", "documents", "answer"}.issubset(benchmark.input_columns):
            # 共同 RAG 文件的参考回答只用于服务端验收，不能混入检索或生成上下文。
            answer_column = "answer"
            benchmark = benchmark.model_copy(
                update={
                    "input_columns": [
                        key for key in benchmark.input_columns if key != answer_column
                    ]
                }
            )
        comparison = ComparisonSpec(
            targets=[FrameworkTarget(name=name) for name in draft.frameworks],
            mode=draft.framework_mode,
            benchmark=benchmark,
            answer_column=answer_column,
        )
        plan = await asyncio.to_thread(
            self.plans.build,
            replace(
                submission,
                intent=state.intent,
                attachments=state.attachments,
                workflow="framework",
                comparison=comparison,
            ),
        )
        return PlanningOutcome(
            snapshot,
            "框架对比计划已生成，各分支保留独立来源、环境和结果。"
            + (
                "未提供数据集，将使用明确标注的共同合成检索样例。"
                if comparison.mode == "benchmark" and benchmark is None
                else ""
            )
            + "完成后会在聊天中返回报告。可在右侧运行。",
            plan=plan,
        )

    async def _data(
        self,
        submission: PlanSubmission,
        snapshot: Conversation,
        state: PendingPlanning,
        draft: IntentDraft,
    ) -> PlanningOutcome:
        datasets = [
            self.uploads.repository.uploads.get(key, snapshot.owner_id)
            for key in (state.attachments or snapshot.uploads)
        ]
        datasets = [
            item
            for item in datasets
            if item.name.lower().endswith((".csv", ".tsv", ".json", ".jsonl"))
            and (not draft.dataset_name or item.name == draft.dataset_name)
        ]
        if len(datasets) != 1:
            return planning_question(snapshot, state, "请上传一个数据集，或指出要分析的文件名。")
        plan = await asyncio.to_thread(
            self.plans.build,
            replace(submission, intent=state.intent, workflow="data", attachments=[datasets[0].id]),
        )
        return PlanningOutcome(snapshot, "数据分析计划已生成，可运行统计分析与图表。", plan=plan)

    def _uploaded_code(self, state: PendingPlanning, owner: str) -> str | None:
        sources = [self.uploads.repository.uploads.get(key, owner) for key in state.attachments]
        programs = [record for record in sources if record.name.lower().endswith(".py")]
        if len(programs) != 1:
            return None
        _, raw = self.uploads.content(programs[0].id, owner)
        try:
            return raw.decode("utf-8-sig")
        except UnicodeError:
            raise ContractError("uploaded Python source must be UTF-8") from None

    async def _materials(
        self,
        submission: PlanSubmission,
        snapshot: Conversation,
        state: PendingPlanning,
        draft: IntentDraft,
    ) -> PlanningOutcome:
        records = [
            self.uploads.repository.uploads.get(key, snapshot.owner_id) for key in state.attachments
        ]
        documents = [
            record.id for record in records if record.name.lower().endswith((".pdf", ".txt", ".md"))
        ]
        try:
            documents, snapshot, question = await self._documents(snapshot, state, draft, documents)
        except ContractError:
            return planning_question(
                snapshot,
                state,
                "论文暂时无法下载，请上传 PDF 或补充可用的 arXiv 链接，我会继续规划。",
            )
        if question:
            return planning_question(snapshot, state, question)
        if draft.workflow == "paper":
            plan = await asyncio.to_thread(
                self.plans.build,
                replace(
                    submission,
                    intent=state.intent,
                    workflow="paper",
                    attachments=documents,
                    auto_design=True,
                ),
            )
            return PlanningOutcome(
                snapshot, "论文阅读计划已生成，运行后会解析原文并回答你的问题。", plan=plan
            )
        return await self._research(submission, snapshot, state, draft, documents)

    async def _documents(
        self,
        snapshot: Conversation,
        state: PendingPlanning,
        draft: IntentDraft,
        documents: list[str],
    ) -> tuple[list[str], Conversation, str]:
        # 推测的可选论文地址不构成用户材料需求，也不能触发外部获取。
        paper_url = draft.paper_url if draft.paper_url and draft.paper_url in state.intent else None
        if not documents and paper_url:
            identifier, snapshot = await self.sources.download(snapshot, paper_url)
            documents.append(identifier)
            state.attachments.append(identifier)
        if draft.workflow == "paper" and not documents:
            outcome = await self.sources.paper(snapshot, state, draft)
            if outcome.pending:
                return documents, outcome.snapshot, outcome.response
            documents = [state.attachments[-1]]
            snapshot = outcome.snapshot
        return documents, snapshot, ""

    async def _research(
        self,
        submission: PlanSubmission,
        snapshot: Conversation,
        state: PendingPlanning,
        draft: IntentDraft,
        documents: list[str],
    ) -> PlanningOutcome:
        benchmark, question = await self._benchmark(snapshot, state, draft)
        if question:
            return planning_question(snapshot, state, question)
        fenced = re.search(r"```(?:python|py)?\s*\n(.*?)```", state.intent, re.DOTALL)
        code = submission.code or self._uploaded_code(state, snapshot.owner_id)
        if code is None and fenced:
            code = fenced[1]
        repository, question = await self.sources.repository(
            state, draft, bool(documents) or bool(code)
        )
        if question:
            return planning_question(snapshot, state, question)
        research = ResearchSpec(repository=repository, benchmark=benchmark, max_repairs=2)
        plan = await asyncio.to_thread(
            self.plans.build,
            replace(
                submission,
                intent=state.intent,
                attachments=documents,
                workflow="benchmark" if benchmark else "reproduce",
                research=research,
                code=code,
                auto_design=True,
            ),
        )
        if repository and (draft.repository_choice or repository_urls(state.intent)):
            for node in plan.nodes:
                if node.type == "repository_fetch":
                    node.inputs["auto_discover"] = False
        source_text = (
            f"\n仓库：{repository.url}"
            if repository
            else ("\n使用提供的 Python 源码。" if code else "\n将从论文中发现并核验仓库。")
        )
        mapping = (
            f"\n输入：{', '.join(benchmark.input_columns)}；目标：{benchmark.target_column}。"
            if benchmark
            else ""
        )
        return PlanningOutcome(
            snapshot,
            "实验计划已生成。系统会根据真实源码自动设计入口、锁定依赖并冻结验收规则；可在右侧运行。"
            + source_text
            + mapping,
            plan=plan,
        )

    async def _benchmark(
        self, snapshot: Conversation, state: PendingPlanning, draft: IntentDraft
    ) -> tuple[BenchmarkSpec | None, str]:
        if draft.workflow != "benchmark":
            return None, ""
        profiles = await asyncio.to_thread(
            dataset_profiles,
            self.uploads,
            state.attachments,
            snapshot.owner_id,
            self.settings.benchmark_max_rows,
        )
        if draft.dataset_name and draft.dataset_name in state.intent:
            profiles = [item for item in profiles if item.record.name == draft.dataset_name]
        if len(profiles) != 1:
            names = ", ".join(item.record.name for item in profiles)
            return (
                None,
                "请上传一个评测数据集，或在聊天中指出要使用的文件。"
                + ("当前文件：" + names if names else ""),
            )
        return benchmark_mapping(profiles[0], draft.model_dump(), state.intent)
