# 小型实验与 Benchmark 协议

## 最小示例

examples/benchmark.csv 为四条协议测试数据，examples/openresearch_run.py 根据输入特征生成预测。它用于验收管线，不是论文数据或效果证据。不要在宿主机直接运行实验入口；通过计划传给沙箱。

在工作台「高级实验设置」上传该 CSV，输入列 x、目标列 y、样本 ID 列 id、任务分类；粘贴示例代码，设置 accuracy 最低值 1，消融参数 --disable-parity，生成并执行。真实结果应为基线 1.0、消融 0.5，阈值标准在执行前冻结。scripts/verify_deployment.py 自动执行相同协议并下载真实报告。

## 直接运行真实仓库的测试

普通对话无需填写高级表单。已有 pytest 套件覆盖用户目标时，自动设计选择真实的 `test_paths`，服务端生成固定启动器，在锁定依赖的沙箱中通过 `pytest.main` 执行完整文件，保留实际通过数、失败回溯和退出码。代码生成模型不需要重新编写这套测试。

`test_paths` 最多包含 8 个固定仓库中存在的相对 `.py` 路径，不允许目录跳转或 `::case` 子集选择；选中的测试文件不能成为补丁路径。启动器不允许被替换或添加筛选条件，文件参数置于 `--` 后。此模式不能替代 Benchmark、消融或自定义命令行参数；需要新实验逻辑的请求仍使用模型适配器和有界自动修复。

## 输入与输出

CSV / TSV 要有唯一非空列名；JSONL 每行是一个对象。显式指定特征列、目标列和可选 ID 列；目标列和 ID 列不能作为特征。没有 ID 列时用零起始行号字符串。默认最多 2000 行，拒绝空集、重复 ID、缺少字段和超预算数据。

容器内 OPENRESEARCH_INPUT 指向 JSON 数组，每项为：

```json
{"id":"a","inputs":{"x":"1"}}
```

入口向 OPENRESEARCH_PREDICTIONS 写入 JSON 数组，每项只包含：

```json
{"id":"a","prediction":"yes"}
```

每个输入恰好一个预测，顺序不限；缺失、重复、未知 ID 和非有限回归数值均失败。实验不能访问目标文件，目标由服务端评估持有。这是列映射隔离，不构成完整防泄漏研究协议；真实数据拆分、预处理与实验设计仍需用户明确声明。

分类支持 accuracy 与 macro_f1，回归支持 mse、mae、r2。服务端根据完整预测重算，不接受实验自报分数。消融复用最终代码和依赖锁，仅改变声明的 argv；报告包含原始分数及基线差值。

## 仓库、依赖与补丁

分支仅在准备时解析一次，保存完整 commit 和文件 SHA。依赖要求完整准确版本闭包，限制 CPython 3.12 / Linux x86_64 wheel，不支持 extras、URL、源码构建和任意安装脚本。运行前离线安装并校验实际发行包版本。

默认分支 `HEAD` 遇到 GitHub 元数据 API 配额耗尽时，通过 [Git smart HTTP 的引用发现协议](https://git-scm.com/docs/http-protocol#_smart_clients)读取真实 HEAD，再下载固定 SHA 的归档。不会猜测 main/master，也不会替换显式分支或标签；完整 SHA 直接下载归档。

补丁仅限 allowed_patch_paths，最多 3 个文件、总量 128 KiB。每个文件须匹配当前基础 SHA；先验证所有路径与 Python 语法再提交。最多 2 次修复，每次启动新容器；所有尝试保存累计可回放补丁与真实输出。失败也保留报告及证据，不转换为成功。

## 主张验收

rubric 在运行前规范化、编号、保存 SHA。来源引用必须逐字出现在声明上传文件的对应页。数值准则指定 metric_name、expected_value、operator、tolerance 和 variant。准则可为语义描述，但需要受约束的模型调用与已有执行证据。

状态包括 verified、partially_reproduced、contradicted、unverifiable、blocked_by_missing_asset。空准则、没有配置模型或缺少资产不会产生论文已复现结论。所有判定仅适用于声明的数据、参数与运行环境。
