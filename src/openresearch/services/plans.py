from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.runtime import Plan
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.code import build_code_plan
from openresearch.planner.data import build_data_plan
from openresearch.planner.documents import build_document_plan, prepend_documents
from openresearch.planner.framework import build_framework_plan
from openresearch.planner.research import build_research_plan
from openresearch.storage.repository import Repository


class PlanService:
    def __init__(self, settings: Settings, repository: Repository) -> None:
        self.settings = settings
        self.repository = repository

    def create(self, submission: PlanSubmission) -> Plan:
        plan = self.build(submission)
        self.repository.save_plan(plan)
        return plan

    def build(self, submission: PlanSubmission) -> Plan:
        """先校验附件归属，再按工作流构建已验证的持久化计划。"""
        if submission.result_gap_repair and (
            submission.comparison is not None
            or submission.workflow not in {"auto", "reproduce", "benchmark"}
            or (submission.workflow == "auto" and submission.research is None)
        ):
            raise ContractError("result-gap repair requires a scientific execution workflow")
        for identifier in submission.attachments:
            self.repository.uploads.get(identifier, submission.identity.user_id)
        if len(set(submission.attachments)) != len(submission.attachments):
            raise ContractError("duplicate attachments")
        if submission.workflow == "framework" or submission.comparison is not None:
            if submission.comparison and submission.comparison.benchmark:
                self.repository.uploads.get(
                    submission.comparison.benchmark.upload_id, submission.identity.user_id
                )
            return build_framework_plan(submission, self.settings)
        if submission.workflow == "data":
            return build_data_plan(submission, self.settings)
        if submission.research and submission.research.benchmark:
            self.repository.uploads.get(
                submission.research.benchmark.upload_id, submission.identity.user_id
            )
        if submission.workflow in {"reproduce", "benchmark"} or submission.research:
            plan = build_research_plan(submission, self.settings)
            return plan
        is_document = submission.workflow == "paper" or (
            submission.workflow == "auto"
            and submission.attachments
            and submission.code is None
            and "smoke" not in submission.intent.lower()
        )
        if is_document:
            plan = build_document_plan(submission, self.settings)
        else:
            plan = build_code_plan(
                submission.intent,
                submission.identity.user_id,
                submission.identity.session_id,
                self.settings,
                submission.code,
            )
            plan.attachments = submission.attachments
            if plan.attachments:
                plan = prepend_documents(plan, self.settings)
        return plan
