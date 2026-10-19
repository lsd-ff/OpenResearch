import pytest

from openresearch.services.source_queries import named_source_query, source_queries


@pytest.mark.parametrize(
    "intent,expected",
    [
        ("复现一下 Transformer 论文的核心架构并跑通测试", "Transformer"),
        ("复现《Attention Is All You Need》的核心架构", "Attention Is All You Need"),
        ("分析 Attention Is All You Need 这篇论文", "Attention Is All You Need"),
        ("帮我复现这个实验", ""),
        ("执行 CPU smoke", ""),
        ("执行代码\n```python\nprint('never search code')\n```", ""),
    ],
)
def test_source_query_preserves_human_names_without_inventing_urls(intent, expected):
    assert named_source_query(intent) == expected


def test_search_has_one_human_name_fallback_and_deduplicates():
    intent = "复现一下 Transformer 论文的核心架构并跑通测试"
    assert source_queries("Transformer: Attention Is All You Need", intent) == [
        "Transformer: Attention Is All You Need",
        "Transformer",
    ]
    assert source_queries("Transformer", intent) == ["Transformer"]
