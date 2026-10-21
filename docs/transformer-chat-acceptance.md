# Transformer 单句对话验收报告

本页保留 2026-10-19 的独立回执；最新新会话 18 项测试、整合部署及报告输入修正见 [2026-10-21 本地整合记录](local-verification-2026-10-21.md)。

2026-10-19：在空白工作台会话中仅发送「复现一下 Transformer 论文的核心架构并跑通测试」，系统完成自动检索、源码核验、测试设计、依赖锁定、规则冻结、容器执行与中文报告回传。

**最终结果：18 项测试一次通过，耗时 3.95 秒，退出码 0；8/8 节点完成，3/3 冻结功能规则核验通过。** 没有上传代码、论文或数据，没有填写高级实验设置，没有手动点击运行或重试。

## 实际执行记录

- 计划：`a3413e20ca64435b98dde2726bbc3c04`，会话：`session-eee6da0d-ab1f-4b8e-97b1-36de8985ea02`。
- 工作台：[打开执行记录](http://127.0.0.1:8080/?plan=a3413e20ca64435b98dde2726bbc3c04)。链接沿用浏览器身份，服务端验证资源归属。
- 规划策略为 `llm`；执行图北京时间 15:33:04–15:34:57，约 113 秒，不含此前意图识别与规划时间。每个节点只运行一次，事件中没有 task_failed / plan_failed。
- 本轮沙箱仅 1 次程序执行，exit_code=0、stderr 为空。规则冻结完成事件序号 1230，准备入口开始事件序号 1234，未在看到结果后改写验收条件。
- 刷新页面后仍显示完整流程图和聊天报告，报告没有重复追加。

## 来源与执行方式

系统最初发现的 NumPy 实现没有可收集的 pytest 套件，随后继续实际检索，选中 [Tim-Xiong/transformer_from_scratch](https://github.com/Tim-Xiong/transformer_from_scratch/tree/97b4b0668696df71c6ddc0c6ed7ad412c8157cd6)，固定提交 `97b4b0668696df71c6ddc0c6ed7ad412c8157cd6`。

[完整 Transformer 源码](https://github.com/Tim-Xiong/transformer_from_scratch/blob/97b4b0668696df71c6ddc0c6ed7ad412c8157cd6/transformer.py)包含 Encoder、Decoder、交叉注意力、位置编码、前馈层和 LayerNorm。本轮直接运行该提交的 [原始测试套件](https://github.com/Tim-Xiong/transformer_from_scratch/blob/97b4b0668696df71c6ddc0c6ed7ad412c8157cd6/test_transformer.py)，没有替换模型实现或修改原测试。

模型自动选择 `test_paths=["test_transformer.py"]`；服务端生成的入口仅负责启动完整套件、保留输出并传回真实退出码：

```text
import pytest

raise SystemExit(pytest.main(['-v', '-s', '--tb=short', '--', 'test_transformer.py']))
```

该模式不要求模型重新编写已有测试。选中的测试文件不能成为补丁路径，修复也不能替换启动器或添加用例筛选。新实验逻辑仍使用受范围与摘要约束的代码生成和自动修复。

## 测试结果

| 覆盖范围 | 数量 | 实际结果 |
| --- | ---: | --- |
| LayerNorm：形状与归一化统计 | 2 | PASSED |
| 多头注意力：分头、输出形状、mask | 3 | PASSED |
| 前馈层：形状与 ReLU | 2 | PASSED |
| 正弦位置编码：形状与周期关系 | 2 | PASSED |
| EncoderLayer：带 mask 与不带 mask | 2 | PASSED |
| DecoderLayer：带 mask 与不带 mask | 2 | PASSED |
| Transformer：Encoder、Decoder、完整前向、Encoder-only | 4 | PASSED |
| 整体集成测试 | 1 | PASSED |
| 合计 | **18** | **18 passed in 3.95s** |

真实 stdout 摘录：

```text
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.7.0
collecting ... collected 18 items
test_transformer.py::TestMultiHeadAttention::test_attention_mask PASSED
test_transformer.py::TestDecoderLayer::test_with_masks PASSED
test_transformer.py::TestTransformer::test_full_transformer_output_shape PASSED
test_transformer.py::test_integration PASSED
============================== 18 passed in 3.95s ==============================
```

冻结的三项功能规则分别验证核心模块、带 mask 的 Encoder/Decoder，以及完整 Transformer 前向与集成测试，最终均为 verified。它们是本轮用户目标的功能验收，不能代表论文全部科学主张。

## 环境与可追溯性

运行于真实 Docker 沙箱，Python 3.12.13、Linux x86_64、NumPy 2.5.4、pytest 9.1.1。其余锁定依赖为 pluggy 1.7.0、packaging 26.3、pygments 2.21.0、iniconfig 2.3.1。依赖在服务端校验摘要后，于无实验出站网络的容器内离线安装；配置限制为 1 CPU、1 GiB 内存。

| 对象 | SHA-256 |
| --- | --- |
| 原始模型 `transformer.py` | `41c21e2f80a9a720c967222930351bb081c6eda2677056e838d22328226326f0` |
| 原始测试 `test_transformer.py` | `320736641d8ff9eb71c65b024120c065c8667ed8210b3e36c820f34d02ff9cfe` |
| 实际执行启动器 | `62bd973896f84dcb5ef16dc5ea04765ffdf95e107c119cefcba90d3f9ea5171b` |
| 执行前冻结规则 | `d37a6ead9555ffec7a429845c795e17c0f78a9fe567d8e9b2e11d5544f88a189` |

所有产物回执已按服务端保存的 SHA-256 核对。本地完整回执在 `.data/natural-acceptance/session-eee6da0d-ab1f-4b8e-97b1-36de8985ea02/`，包含原始 stdout/stderr、补丁、依赖锁、规则、事件和最终报告；运行数据与密钥不提交仓库。

## 本轮修复与工程检查

- 检查真实 Python API、返回表达式和完整架构，拒绝把 Notebook-only 或 decoder-only GPT 当作完整原始 Transformer。
- 自动发现的候选下载失败或超预算时，进行有限的真实候选检索；保留用户显式指定的仓库和版本约束。
- 请求测试时优先发现同名方法的真实 pytest 套件，避免不必要的长段适配代码生成。没有合适套件时，仍保留可用原始来源与自定义适配能力。
- GitHub 元数据 API 配额耗尽时，通过官方只读 Git 引用发现获取真实默认 HEAD；不猜测分支，不改变显式版本要求。
- 真实 stdout/stderr 进入有界修复，汇总保留所有失败尝试。历史失败记录不删除，也不把后续成功写成全程没有失败。
- 完成报告自动回到聊天，支持失败恢复、刷新去重和长会话列表滚动。

本机后端 Ruff、格式检查、严格 mypy、**327 项测试**及 wheel/sdist 构建通过；**22 项真实 Docker/HTTP/OpenSandbox/外部来源验收全部通过**。前端 ESLint、TypeScript、Vite 构建及 **10 项 Chromium 网页回归通过**，包含保留 40 条历史会话时当前报告与输入框仍可见。API、Worker、Sandbox、Intent 四服务的 155 个 Python 源文件与工作区摘要一致。

此前的 [显式脚本复跑报告](transformer-reproduction.md) 属于提供完整测试脚本后的执行，不能用于本页的单句自然对话验收。此前失败、取消和汇总错误的记录均保留；本页数字仅取自上述最终新会话。

## 范围与复跑

本轮验证核心架构、前向传播和原仓库测试；没有训练、损失收敛、WMT 数据集或 BLEU 结果，也未与论文训练权重或其他实现做逐值/性能对照。

使用已配置模型的工作台，新建空白会话并发送同一句话即可复跑。下一次运行重新固定实际来源与依赖，耗时和结果以该次回执为准；网络、模型响应或测试失败会保留错误状态。
