import hashlib
import json
from uuid import uuid4

from openresearch.executor.models import DAG, ExecutorConfig
from openresearch.executor.receipts import (
    AttemptBudgetExceeded,
    AttemptReceipt,
    GraphReceipt,
    NodeReceipt,
)
from openresearch.storage.localpath import filesystem_path


class RunJournal:
    def __init__(self, config: ExecutorConfig, graph: DAG) -> None:
        self.config = config
        self.id = uuid4().hex
        self.root = filesystem_path(config.sandbox.data_dir) / "executor" / self.id
        self.root.mkdir(parents=True, exist_ok=False)
        self.receipt = GraphReceipt(
            self.id, [NodeReceipt(node.id) for node in graph.nodes], self.root / "receipt.json"
        )
        (self.root / "graph.json").write_text(graph.model_dump_json(indent=2), encoding="utf-8")
        self.attempts_reserved = 0
        self.flush()

    def resolve(self, node_id: str, commands: tuple[str, ...]) -> None:
        key = hashlib.sha256(node_id.encode()).hexdigest()
        (self.root / f"commands-{key}.json").write_text(
            json.dumps({"node_id": node_id, "commands": commands}, ensure_ascii=False),
            encoding="utf-8",
        )

    def reserve(
        self, node: NodeReceipt, commands: tuple[str, ...], version: int
    ) -> list[AttemptReceipt]:
        count = len(commands)
        if (
            len(node.attempts) + count > self.config.max_attempts_per_node
            or self.attempts_reserved + count > self.config.max_attempts
        ):
            raise AttemptBudgetExceeded("cumulative candidate attempt budget exceeded")
        self.attempts_reserved += count
        attempts = [
            AttemptReceipt(
                uuid4().hex,
                index,
                node.rounds,
                version,
                hashlib.sha256(command.encode()).hexdigest(),
                self.root / "attempts" / uuid4().hex,
            )
            for index, command in enumerate(commands)
        ]
        node.attempts.extend(attempts)
        self.flush()
        return attempts

    def flush(self) -> None:
        temporary = self.receipt.audit_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.receipt.as_dict(), ensure_ascii=False, default=str, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.receipt.audit_path)
