"""持久化入口：组合各存储职责，保持应用层不依赖 SQLAlchemy 表结构。"""

from openresearch.storage.claims import TaskClaims
from openresearch.storage.conversations import ConversationStore
from openresearch.storage.database import Database
from openresearch.storage.execution_store import ExecutionStore
from openresearch.storage.journal_store import JournalStore
from openresearch.storage.plan_store import PlanStore
from openresearch.storage.sandbox_store import SandboxStore
from openresearch.storage.task_commands import TaskCommands
from openresearch.storage.transitions import Transitions
from openresearch.storage.uploads import UploadStore


class Repository:
    def __init__(self, database_url: str) -> None:
        self.database = Database(database_url)
        self.sessions = ConversationStore(self.database)
        self.uploads = UploadStore(self.database, self.sessions)
        transitions = Transitions(self.database)
        self.claim_next = TaskClaims(self.database, transitions).claim_next
        self._bind_plans(PlanStore(self.database, transitions))
        self._bind_executions(ExecutionStore(self.database, transitions))
        self._bind_sandboxes(SandboxStore(self.database))
        journal = JournalStore(self.database)
        self.list_events = journal.list_events
        self.get_artifact = journal.get_artifact
        self.close = self.database.close
        self.upgrade = self.database.upgrade
        self.tasks = TaskCommands(self.database)

    def _bind_plans(self, store: PlanStore) -> None:
        self.save_plan = store.save_plan
        self.get_plan = store.get_plan
        self.queue_plan = store.queue_plan
        self.approve = store.approve
        self.cancel = store.cancel
        self.retry = store.retry

    def _bind_executions(self, store: ExecutionStore) -> None:
        self.renew = store.renew
        self.finish = store.finish
        self.append_log = store.append_log
        self.recover_expired = store.recover_expired

    def _bind_sandboxes(self, store: SandboxStore) -> None:
        self.register_sandbox = store.register_sandbox
        self.mark_sandbox_closed = store.mark_sandbox_closed
        self.cleanup_candidates = store.cleanup_candidates
        self.execution_live = store.execution_live
