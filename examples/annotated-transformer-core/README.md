# 固定 Annotated Transformer 的核心功能验收

上游为 [harvardnlp/annotated-transformer](https://github.com/harvardnlp/annotated-transformer/tree/debc9fd747bb2123160a98046ad1c2d4da44a567)，固定提交 `debc9fd747bb2123160a98046ad1c2d4da44a567`。原始 `the_annotated_transformer.py` 的 SHA-256 为 `9562df56fc29a172dd9284e0d2ba68443b25e8733981fedf854d1f36cbc2e74b`。

校验器先核对原文件 SHA，再通过 AST 直接加载原有核心类和函数，保留函数体；数据下载、教程训练及可视化示例不进入此入口。没有手写替代 Transformer。十二项 unittest 检查覆盖：因果掩码、缩放点积注意力、未来值隔离、多头形状、前馈层、残差/归一化、嵌入/位置编码、完整 Encoder/Decoder、输出概率、前缀不变性、交叉注意力的源依赖及反向梯度。

CPU 功能模型使用两层、d_model=16、d_ff=32、两头注意力和 11/13 的源/目标词表，dropout 为零、随机种子为 2026。这些尺寸用于验证核心行为及梯度。

## 运行

需要 Docker、公开 GitHub/PyTorch/PyPI 网络与已安装的项目依赖；不调用模型。先构建独立 CPU 验证镜像：

```powershell
docker build -t openresearch-reference-cpu:2.7.1 examples/annotated-transformer-core
$referenceImage = docker image inspect openresearch-reference-cpu:2.7.1 --format '{{.Id}}'
uv run --locked python examples/annotated-transformer-core/verify.py --image $referenceImage
```

CPU 包来源使用 [PyTorch 官方版本安装索引](https://pytorch.org/get-started/previous-versions/)。镜像固定 Torch 2.7.1+cpu，传入实际镜像 ID 后，计划继续校验运行时身份；具体依赖版本以该镜像和实际输出为准。这个核心验收没有安装上游旧版 CUDA/TorchText/训练工具组合，也没有验证整本教程、WMT14 训练或 BLEU 分数。

`verify.py` 在独立数据目录启动真实 API 和 Worker，使用原生科研计划及审批入口。仓库全部文件固定并缓存；代码、依赖锁及规则进入原有持久流程，十二项检查只在 Docker 内运行。预先准备的 CPU 镜像通过命名运行时提供 Torch，执行阶段的容器仍限制为 1 CPU、1 GiB 内存且不联网。普通项目默认镜像和现有运行时注册不改变。

实际程序输出、完整计划、源码清单和摘要保存在 `.data/reference-core-*/`；使用 `--output` 指定另一个独立目录。重读不再执行，失败和镜像安装错误保持失败。不要将目录中的运行数据或镜像提交到 Git。

该提交包含 18 个文件、17,351,506 字节，最大 HTML 为 9,453,410 字节，Notebook 为 7,526,242 字节，Python 源码为 66,249 字节。完整文件及 SHA 保留，不丢弃 Notebook/HTML。默认来源限制为：压缩归档 16 MiB、单文件 16 MiB、全部源码 32 MiB、2,000 个文件；明确配置更小预算时仍会拒绝。源码摘要独立限制为每文件 128 KiB AST 输入，保持 24,000 字节的 API 摘要总预算。

2026-10-21 本机实际计划 `8a7878df1beb49a98a8ef13e4c5a8a51` 使用镜像 `sha256:68093db5b08d89644961ad233abeb0a703c3fc426ff5a22a449bece1fa316093`，十二项检查零失败/错误/跳过。正式计划创建两个容器，镜像构建另计；退出后所属命名空间无遗留容器，重读未追加尝试。此记录是核心功能验收，独立于先前空白聊天启动另一真实实现的十八项原始测试。
