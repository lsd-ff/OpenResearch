import pytest

from openresearch.sources.dependency_closure import validate_dependency_graph


def test_extra_activated_after_package_was_seen_is_still_fully_validated():
    graph = {
        "sqlalchemy": ["greenlet>=3; extra == 'asyncio'"],
        "framework": ["sqlalchemy[asyncio]>=2"],
        "greenlet": [],
    }
    pins = {"sqlalchemy": "2.0", "framework": "1.0", "greenlet": "3.0"}
    validate_dependency_graph(graph, pins, {})
    with pytest.raises(ValueError, match="greenlet"):
        validate_dependency_graph(graph, {**pins, "greenlet": "2.0"}, {})
    with pytest.raises(ValueError, match="greenlet"):
        validate_dependency_graph(
            graph, {key: value for key, value in pins.items() if key != "greenlet"}, {}
        )


def test_optional_extra_does_not_activate_without_a_real_dependency_request():
    validate_dependency_graph({"library": ["cuda>=1; extra == 'gpu'"]}, {"library": "1"}, {})


def test_extra_cycle_reaches_fixed_point_and_cannot_hide_unpinned_urls():
    graph = {"first": ["second[a]>=1"], "second": ["first[b]>=1; extra == 'a'"]}
    validate_dependency_graph(graph, {"first": "1", "second": "1"}, {})
    graph["first"].append("secret @ https://example.invalid/a.whl ; extra == 'b'")
    with pytest.raises(ValueError, match="exact pin"):
        validate_dependency_graph(graph, {"first": "1", "second": "1"}, {})
