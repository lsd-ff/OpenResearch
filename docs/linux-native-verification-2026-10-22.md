# Linux 原生发行验收记录

2026-10-22（北京时间）：用户要求的 Linux 原生发行补验已完成，CI 暂不处理。实际验证源码为 `1908f841a5286e0ca0239afc67d5bebd67a01e23`，包含显式依赖代理与标准输入线程退出两处修复。Go 对照保持只读 `50348231852d7dac36b52f643d59a3f2af59d20a`。前次功能迁移、网页、GPU 和单句 Transformer 证据仍见 [本地整合记录](local-verification-2026-10-21.md)，没有将那些历史用例冒充本次重跑。

## 实际环境与源码

| 项目 | 实际值 |
| --- | --- |
| Linux 主机环境 | WSL2 Ubuntu 24.04.4 LTS，x86_64 |
| 内核 / libc | `6.18.54.1-microsoft-standard-WSL2` / glibc 2.39 |
| 构建解释器 | Ubuntu CPython 3.12.3 |
| 构建工具 | uv 0.11.33，PyInstaller 6.22.3，依赖由 `uv.lock` 固定 |
| Docker | 本地 `/var/run/docker.sock`，实际 Linux/amd64 daemon |
| Windows 回归 | CPython 3.13.5、独立 CPython 3.14.5；Windows 发行环境另按锁安装 |

应用及构建工具在 Ubuntu 进程中运行，生成并实际启动 64 位 x86-64 ELF；实验进入受管 Linux Docker 容器。它不是 Windows exe，也没有用“容器内执行过 Python”代替 Linux 主机上的发行验收。WSL2 仍是本次明确的宿主边界，没有验证物理 Linux 主机、其它发行版、其它 glibc 或 ARM。

原生临时根为 `/home/lsd/.cache/openresearch-linux-native/fbb3e8ffa99a4230bb87406ea5c1d435`。初始输入来自干净 Git 归档及逐个 SHA 核验的前端构建文件，不复制项目 `.env`、数据库或虚拟环境。两处修正逐文件传入并留存摘要，最终与 `1908f84` 的 Git 归档比对内容；280 个运行 Python 文件的原始 SHA 与构建清单逐一一致。其中六个修改文件与 Git blob 仅 CRLF/LF 不同，原始两组 SHA 和换行计数明确保存在发行包 `verification.json`，没有把它们记录成逐字节相同。其它运行资源及前端摘要均实际核对。

初次 WSL 下载过慢，构建环境由 51 个适配 Linux 且与 `uv.lock` 的官方 URL、体积和 SHA 一致的 wheel 种入缓存，再由原生 uv 按锁同步；没有修改锁或使用 Windows site-packages，构建与测试均使用原生 Linux Python。首次慢速安装进程在替代原生环境验证后已终止；来源网络转发另述。

## 最终门禁与实际运行

| 检查 | 结果 |
| --- | --- |
| Ubuntu CPython 3.12.3 默认门禁 | **870 通过**，56.55 秒；Ruff、格式、严格 mypy（280 源文件）、wheel/sdist 通过 |
| Windows CPython 3.13.5 默认门禁 | **870 通过**，92.87 秒；同一质量及构建门禁通过 |
| Windows CPython 3.14.5 测试 | **870 通过**，72.12 秒 |
| 原生 Linux Docker 专项 | **10 通过**，75.72 秒：真实 TTY、命令退出码、中断、超时、检查点恢复、Pin 分支、API/Worker 独立进程和 HTTP 沙箱 |
| 显式代理修正后实际 PyPI/Docker 回归 | **4 通过**，70.54 秒：公开依赖、初始准备恢复与允许/固定 Python 运行时 |
| 退出修正后针对性行为回归 | **12 通过**，17.68 秒，包含真实解释器子进程持有 stdin 及 EOF |
| 最终 Linux 归档检查 | **2462 项**；实际资源与清单一致；另从 Windows 使用真实本机配置密钥交叉检查，未发现凭据 |
| 最终 Linux ELF 独立运行 | **通过**：实际程序、依赖安装、PDF、EOF 与 SIGTERM 退出、重启恢复 |
| 最终 Windows exe 独立运行 | **通过**：同一实际程序/依赖/PDF/重启流程，两次 EOF 退出 |
| 最终四服务部署 | API、Worker、sandbox、intent 源码各 280 文件摘要一致；17 主 API、7 沙箱、2 旧意图路径和 27 类任务无缺项 |

三个默认测试结果各有 2 项公共 PDF 外网条件跳过，182 项 Docker 在收集时另选，均不计入通过。Linux 最终 ELF 中的实际上传 PDF 与容器解析另外通过。10 项原生 Docker 专项早于这两处修正，涉及的 Core 实现未变；最终冻结程序与 870 项门禁在两处修正后重跑。4 项公开依赖恢复由 Windows 驱动实际 Linux Docker，原生 ELF 的公开依赖解析/安装则由下列独立回执另外证明。这些专项没有冒充完整 184 项矩阵连续重跑。

发行验收把单个程序复制到独立目录，从另一个空工作目录、空 PATH 启动，不提供项目配置、外部 Python/Node/uv 路径或网页目录。实际验证内嵌网页静态字节、API 404、审批、三个代码断言、自带 uv 解析 `blinker==1.9.0`、按哈希下载并在容器中安装/运行三个断言、PDF 文本解析及空白页状态。这里的 PDF 与小程序是功能验收，不是科研效果。

Linux 最终回执 `1c9a579ceb834144b00ac4db0124ae7e`：

- 代码计划：`040cb7362fad48e1aaaec8f8a43e63ec`。
- 依赖计划：`8926f3729fd14499908852c3b8c695d5`。
- PDF 计划：`2b1983e2b52249bc911dcdb883a87b45`。
- `shutdown_modes=["stdin_close","sigterm"]`，两次退出码均为 0，端口释放，所属活动容器为 0，重启后原任务保持完成且没有重跑。

Windows 最新回执 `c2789ca2397b4313850734bdb53b2eef` 同样通过，`shutdown_modes=["stdin_close","stdin_close"]`，原记录不重跑且所属容器为 0。

## 实测失败与修正

第一次原生程序已能启动和执行代码，但冷缓存解析依赖超时。解析器刻意隔离环境时遗漏了管理员明确配置的来源代理。现仅向 uv 投影 `OPENRESEARCH_SOURCE_PROXY_URL`，科研设计、初始环境准备、依赖恢复和框架入口均绑定该配置，切换目标解释器后继续保留。固定 PyPI 索引、禁止构建脚本和凭据隔离不变；非零诊断清除完整代理 URI、编码及解码密码，保留实际退出码与冲突信息。8 条新增行为用例修正前全部失败，修正后相关 38 条通过，随后实际解析及 Docker 恢复通过。

第二次主动补测“stdin 保持打开时发送 SIGTERM”，发现中间 ELF `3bd1668a37a797ca7b8b2eb77cff4a272709bfa530ae4d6c12e3bf82882badab` 在完成实验和持久恢复后仍以 `-6` 退出。原日志包含 `_enter_buffered_busy`，原因是守护线程阻塞在 `sys.stdin.buffer.read()` 并持有解释器终结时所需的缓冲锁。现使用文件描述符 `os.read()`；真实子进程先退出再关闭父端 stdin，确保测试不会提前 EOF 掩盖问题。修正前该行为回归稳定复现失败，修正后通过；最终 Linux ELF 的 SIGTERM 和 Windows 程序均已实测通过。原生失败目录 `9a51578656e84c8f902e2cde10dceedd` 保留。

WSL 本轮来源联网使用仅监听 loopback 的 CONNECT 转发，只允许 `pypi.org:443` 和 `files.pythonhosted.org:443`，通过现有 Windows 来源代理转发不透明 TLS。未替换 PyPI 响应、关闭证书校验或向 Linux 投影模型密钥/私有代理密码；解析和运行仍是原生 Linux 进程。转发是本机网络条件，不应据此声称无需来源网络。验收后已确认所属 Linux helper、Windows wrapper 退出及端口 35527 释放，没有关闭用户 WSL 或 Docker。

## 交付与重验

本地导出目录为 `dist/release/linux-x86_64/`，不提交二进制或运行数据：

| 文件 | 体积 / SHA-256 |
| --- | --- |
| `openresearch` | 51,133,520 字节；`f0e33f0c1ad5c4202adbc816865b5d08713a6fe62bc3ad65757270c2da5c9305` |
| `openresearch-linux-x86_64.tar.gz` | 50,833,705 字节；`44713032aa1c05c8a9a457aa66443d54df9351d8c574e5578482ea57b4576c72` |
| 最新 Windows `dist/release/openresearch.exe` | `95c43f65fd898b777e7c20e51182fa68e2b3442c4f8bd43cc2591c880700ddf3` |

tar.gz 包含程序、`openresearch.manifest.json`、`LICENSE` 和 `verification.json`。已读回 tar 内程序核对最终 SHA，程序权限为 `0755`、其余文件 `0644`。旁置 `SHA256SUMS` 记录 ELF 与压缩包的摘要。Linux 安装机器保存压缩包与旁置摘要后可执行：

```sh
tar -xzf openresearch-linux-x86_64.tar.gz
sha256sum -c SHA256SUMS
./openresearch app
```

Docker 及所用外部服务仍需可达。可执行文件旁 `.env` 与持久数据的约定见 [发行说明](release.md)。源码重验使用目标 Linux 环境的 `uv run --locked python scripts/check.py`，再按发行说明构建、`audit_release.py` 和 `verify_release.py`；原生信号退出已进入正式验收脚本。

本机 8080 四服务已在无活动任务、SQLite 在线备份后更新至镜像 `sha256:d6778c1b9ad5d95e9f2e13429dc292e7deadf3575444e0e17fcf2b6dd166e8eb`。三个 HTTP 服务健康，Worker 运行；历史数据库与上传卷保留，四服务逐字节源码核验通过。

## 原始证据

导出的原生日志位于忽略目录 `.data/linux-native/fbb3e8ffa99a4230bb87406ea5c1d435/verification/`，包括构建、归档、运行、回执、进程日志、环境与 ldd。原始临时根继续保留早期失败。本地 Windows 门禁和回归位于 `.data/linux-native-*.log`。

| 原生日志 | SHA-256 |
| --- | --- |
| `check_v3.log` | `85c3b5e6c087c2122bca98dacb5a392fe752be8b6c40122e65835bf459017e41` |
| `docker.log` | `337e2bfb5ce8ee418e1c8673739329c5509eba9fa67ae66c04d90254b05a28c1` |
| `build_v3.log` | `abd240252a7e34c33d25e8442f45c8fadaa4bb82c95b379f4bdc3231b92763da` |
| `audit_v3.log` | `caa9735520643e7ea30eb7d6ceed43fa220d067dd2393bc3721e82de56a7199b` |
| `verify_v3.log` | `59c2975bdca28b4187d93f45a0a2e4fcb2aebab67d0f947a433df94023889e8c` |

Windows Linux 交叉归档密钥审计为 `.data/linux-native-windows-secret-audit.log`（2462 项），Windows 最终运行日志为 `.data/linux-native-windows-regression-v3.log`。源码/包的当前配置密钥审计为 563 文件干净。部署源码与路径复核保存在 `.data/parity-final-source-audit.json`、`.data/parity-final-route-audit.json`；网络辅助清理回执为 `.data/linux-native-network-cleanup.json`。CI 没有查询、重试或记为通过，也没有发布 GitHub Release。
