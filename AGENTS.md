# OpenResearch Python 开发说明

本仓库执行 `.trae/skills/code_skill/SKILL.md` 中的编码规范。

- 默认用中文沟通；关键的事务、租约、恢复、取消和并发代码使用中文注释。
- 使用 src 包布局、uv.lock、Ruff 和严格 mypy，保持公开契约有类型。
- 优先补充行为测试后小步重构，避免长函数、过大类和重复参数列表。
- 实验代码只能进入沙箱；离线执行替身仅存在于 tests。
- 不把未实现的论文、检索、Benchmark 或工具能力包装成成功结果。
- 不改动 Go 工程作为重写的实现捷径；通过兼容接口或适配器复用。
- 默认质量门禁：`uv run python scripts/check.py`。
- 真实 Docker 验收：设置 `OPENRESEARCH_TEST_DOCKER=1` 后运行 `uv run pytest -m docker`。
