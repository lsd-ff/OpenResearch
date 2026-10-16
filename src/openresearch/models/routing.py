from openresearch.errors import ContractError

ROLES: dict[str, set[str]] = {
    "code_generate": {"coder_agent", "research_coding_agent"},
    "code_execute": {"sandbox_agent", "coder_agent"},
    "document_extract": {"librarian_agent", "data_agent"},
    "paper_analyze": {"librarian_agent", "data_agent"},
    "repository_fetch": {"research_agent", "librarian_agent"},
    "environment_lock": {"research_agent", "sandbox_agent"},
    "claim_freeze": {"research_agent", "librarian_agent", "data_agent"},
    "research_prepare": {"research_agent", "coder_agent", "research_coding_agent"},
    "research_design": {"research_agent", "coder_agent", "research_coding_agent"},
    "research_execute": {"research_agent", "sandbox_agent", "research_coding_agent"},
}


def validate_role(task_type: str, role: str) -> None:
    if role not in ROLES.get(task_type, set()):
        raise ContractError(f"agent {role} cannot perform task type {task_type}")
