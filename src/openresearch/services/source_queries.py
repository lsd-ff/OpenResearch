"""从用户已经给出的名称生成有界检索词；不从模型记忆构造来源 URL。"""

import re

GENERIC_TERMS = {
    "api",
    "benchmark",
    "cpu",
    "csv",
    "github",
    "gpu",
    "json",
    "pdf",
    "python",
    "smoke",
}


def named_source_query(intent: str) -> str:
    text = re.sub(r"```.*?```|https?://\S+", " ", intent, flags=re.S)
    quoted = re.search(r"[《\"]([^》\"\n]{2,160})[》\"]", text)
    if quoted:
        return quoted.group(1).strip()
    names = re.findall(r"[A-Za-z][A-Za-z0-9_.-]*(?:[ \t]+[A-Za-z][A-Za-z0-9_.-]*)*", text)
    selected = [
        name.strip()
        for name in names
        if not all(word.lower() in GENERIC_TERMS for word in name.split())
    ]
    return " ".join(dict.fromkeys(selected))[:160]


def source_queries(proposal: str, intent: str) -> list[str]:
    # 模型查询可能在另一种搜索语法中无效；无结果时退回用户实际给出的名称。
    queries = [proposal.strip(), named_source_query(intent)]
    return list(dict.fromkeys(query for query in queries if query))[:2]
