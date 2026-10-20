from openresearch.errors import ContractError

ROLES: dict[str, set[str]] = {
    "framework_catalog": {"data_agent"},
    "result_audit": {"data_agent"},
    "legacy_environment": {"coder_agent"},
    "runtime_prepare": {"coder_agent"},
    "comparison_protocol": {"data_agent"},
    "framework_design": {"coder_agent"},
    "framework_research": {"librarian_agent"},
    "framework_answer": {"chat_agent"},
    "framework_report": {"data_agent"},
    "chat_answer": {"chat_agent"},
    "source_discover": {"librarian_agent"},
    "data_analyze": {"data_agent"},
    "ablation_design": {"data_agent"},
    "code_generate": {"coder_agent", "research_coding_agent"},
    "code_execute": {"sandbox_agent", "coder_agent"},
    "document_extract": {"librarian_agent", "data_agent"},
    "paper_analyze": {"librarian_agent", "data_agent"},
    "repository_fetch": {"research_agent", "librarian_agent"},
    "environment_lock": {"research_agent", "sandbox_agent", "coder_agent"},
    "claim_freeze": {"research_agent", "librarian_agent", "data_agent"},
    "research_prepare": {"research_agent", "coder_agent", "research_coding_agent"},
    "benchmark_preflight": {"coder_agent", "research_coding_agent"},
    "research_design": {"research_agent", "coder_agent", "research_coding_agent"},
    "research_execute": {"research_agent", "sandbox_agent", "research_coding_agent", "coder_agent"},
}


def canonical_role(task_type: str, role: str) -> str:
    if role == "research_agent":
        if task_type == "repository_fetch":
            return "librarian_agent"
        if task_type == "claim_freeze":
            return "data_agent"
        return "coder_agent"
    if role in {"sandbox_agent", "research_coding_agent"}:
        return "coder_agent"
    return role


def validate_role(task_type: str, role: str) -> None:
    if role not in ROLES.get(task_type, set()):
        raise ContractError(f"agent {role} cannot perform task type {task_type}")
