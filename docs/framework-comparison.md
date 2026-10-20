# 真实框架对比

本功能对应 Go 的多框架资料研究与性能对比分支。Go `coder.go` 的部分确定性脚本带有 `offline_mock_framework_benchmark`、`not_installed_offline_mock` 标记；Python 对比不沿用这类模拟结果。

## 对话与接口

已配置模型并启动 API、Worker、Sandbox 后，可直接发送：

- `对比 LangChain 与 LlamaIndex 的 RAG 性能`：共同协议 → 各框架独立环境/规则/代码 → 最多 8 条预检 → 正式预测 → 共享真实模型回答 → 聊天报告。
- `比较 LangChain 与 LlamaIndex 的功能与适用场景`：实际 PyPI 发布来源 → 模型选择有编号的原文证据 → 来源对比报告；不生成性能结果。

默认自动入队，用户要求只规划或设置了审批时继续等待。显式接口支持 `POST /api/plan` 的 `workflow=framework` 与 `comparison`：

```json
{
  "intent": "对比两个框架的实际检索能力",
  "workflow": "framework",
  "comparison": {
    "mode": "benchmark",
    "answer_mode": "none",
    "targets": [
      {"name": "LangChain", "requirements": ["langchain-core==1.6.12"], "import_roots": ["langchain_core"]},
      {"name": "LlamaIndex", "requirements": ["llama-index-core==0.14.25"], "import_roots": ["llama_index.core"]}
    ]
  }
}
```

`targets` 为 2–4 个不同框架。已提供 LangChain Core / LlamaIndex Core 的真实检索适配器；其他目标由模型提出公开包与实际入口，仍须通过版本、wheel、模块和运行校验。未知包、不可用环境或无效适配器会失败，不能借用另一框架结果。API 可通过 `benchmark` 指定上传数据契约，`answer_column` 指定服务端参考回答，`answer_mode=none` 关闭生成。

未提供数据时使用四条明确构造的问题和四篇短文；不是用户业务数据，也不是论文数据集。两边使用同一 64 维 SHA-256 词嵌入、相同文档及 top-1 检索。生成阶段统一在服务端调用用户配置的真实模型，模型凭据不进入实验容器；它不是各框架默认 RAG 配置的全面比较。

上传的共同 RAG 文件可使用 `id/query/documents/target/answer` 列，其中 `documents` 为 `{id,text}` 数组或该数组的 JSON 字符串，`target` 为预期文档 ID，`answer` 为可选参考回答。参考标签与回答只留在服务端。普通分类/回归数据仍遵守显式列映射；没有参考回答时不计算回答准确率。

## 运行约束与证据

每个框架拥有独立产物命名空间和新容器，不读取另一分支的私有结果。共同协议保存上传 ID、原始 SHA-256、样本数与数据来源。预检绑定代码、数据、规范、环境锁和框架来源/共同辅助代码摘要；预检失败阻断正式运行。

依赖只从 PyPI 固定到完整 `name==version` wheel 闭包，验证平台、版本和 SHA 后离线安装；传递的 extra（例如 `sqlalchemy[asyncio]`）传播至依赖图固定点并再次在容器校验。LangChain 内存向量检索的可选 NumPy 依赖会显式加入锁文件。

可信执行包装记录由适配器触发的安装库函数调用，排除单纯导入初始化；无调用证据不能通过框架使用检查。指标由逐样本预测重算。程序墙钟包含启动、导入、索引与预测，不含安装；模型回答另行记录耗时及规范化精确匹配率。单次观测不构成通用性能排名。

报告等待所有分支终止；失败时仍回传失败、阻塞、缺失产物及已有指标。生成报告不会把整体失败改成成功。取消、审批、执行次数和时间预算继续生效。

## 2026-10-20 实测

真实模型与 Docker 计划 `19f8f5f22f7643a0b3653826fdf15692` 的 14/14 节点完成，数据 SHA-256 为 `6fff1e9235042d6c5ab97be8d4e6b1813de2d1e475c8ef17cf34bb8b3e593a2b`。

| 分支 | 实际固定包 | 完整依赖数 | 检索准确率 | 实际模型回答精确匹配 |
| --- | --- | ---: | ---: | ---: |
| LangChain | langchain-core 1.6.12 | 34 | 1.0 | 1.0 |
| LlamaIndex | llama-index-core 0.14.25 | 62 | 1.0 | 1.0 |

该记录的程序墙钟分别约 3.297 与 7.038 秒，包含当时的追踪包装开销，不用于性能排名。真实回执在忽略目录 `.data/parity-live/framework-eafea9e9b9f24c9fb0c0a93f2070f480/`。

资料研究计划 `3c2ff1569da640e88b926784f2b61c1e` 完成 4/4 节点，两边分别选择 5 与 3 条实际来源片段，服务器绑定原文及来源摘要。回执在 `.data/parity-live/framework-research-c37964026214411994707116f4be37c5/`。

首次实跑中的缺失 NumPy、传递 extra 拒绝、引用无效及开发过程中安装器版本不一致均保留在历史失败回执，后续修复不覆盖历史。上述成功结果只对应明确的功能协议；GPU、其他迁移差距及最终部署验收继续按 [迁移清单](implementation-plan.md) 推进。

最终独立 Docker 回归使用真实 PyPI 来源、两套完整安装环境及当前调用追踪实现，确认实际 `similarity_search` / `retrieve`，通过耗时 199.22 秒；不依赖模型替身生成框架代码或实验结果。默认工程门禁 369 项、严格 mypy（171 个源文件）、打包以及前端 lint/build、11 项网页回归通过。网页中的失败状态用例明确使用状态夹具，不能用于宣称真实框架实验已在部署工作台验收。

远程首次 Docker 回归发现两项旧验收绕过安装器组装入口，已统一使用生产的 `installation_program()`，保留实际 Python 补丁版本 marker 拒绝和小输入盘/大 wheel 安装断言。修复后本机完整 Docker 回归 26 项通过（147.83 秒）；本轮该命令跳过需显式外网/独立服务的三项，其中真实框架来源测试已另行通过，独立 OpenSandbox 和联网仓库测试未在这次回归重跑。最终默认门禁仍为 369 项通过。
