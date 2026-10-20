from pydantic import ValidationError

from openresearch.errors import ContractError
from openresearch.models.discovery import SearchRequest
from openresearch.models.legacy import DirectExecutionRequest
from openresearch.models.repository_discovery import RepositoryDiscoveryRequest
from openresearch.models.sources import RepositorySpec
from openresearch.services.legacy_inputs import text_input

DISCOVERY_INPUTS = {
    "search",
    "preferred_repo_url",
    "repository_ref",
    "paper_arxiv_id",
    "paper_title",
    "paper_search_query",
    "paper_method_name",
}


def discovery_request(
    payload: DirectExecutionRequest, repository: RepositorySpec | None
) -> RepositoryDiscoveryRequest:
    try:
        reference = text_input(payload.inputs, "repository_ref", limit=128)
        reference = reference or (repository.ref if repository else "HEAD")
        preferred = text_input(payload.inputs, "preferred_repo_url", limit=4096)
        if preferred:
            declared = RepositorySpec(url=preferred, ref=reference)
            if repository and repository != declared:
                raise ContractError("conflicting preferred repository declarations")
            repository = declared
        search = (
            SearchRequest.model_validate(payload.inputs["search"])
            if "search" in payload.inputs
            else None
        )
        if search is None:
            for key in ("paper_arxiv_id", "paper_title", "paper_search_query", "paper_method_name"):
                if query := text_input(payload.inputs, key, limit=300):
                    search = SearchRequest(kind="repository", query=query)
                    break
        return RepositoryDiscoveryRequest(search=search, preferred=repository, ref=reference)
    except ValidationError as exc:
        raise ContractError("invalid repository discovery declaration: " + str(exc)) from exc
