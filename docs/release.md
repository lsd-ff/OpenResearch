# 单文件发行与本地一体化启动

`openresearch app` 在一个启动入口中运行工作台、API、持久 Worker 和本地 HTTP 沙箱；发行物不需要目标机器安装 Python、Node 或 uv。实验仍需要 Docker Engine / Docker Desktop 的 Linux 容器，模型功能需要外部模型配置，依赖解析与下载需要相应网络。单文件运行时会解压自带资源到临时目录，实验程序始终在受管容器中执行。

## 构建

在目标操作系统上使用 Python 3.12–3.14、uv 与 Node.js 22：

```powershell
uv sync --locked --group release
npm --prefix frontend ci --no-audit --no-fund
uv run --locked --group release python scripts/build_release.py
```

Windows 生成 `dist/release/openresearch.exe`，Linux 生成 `dist/release/openresearch`。不跨操作系统复用二进制。已构建网页时可用 `--skip-frontend`，构建仍要求实际 `index.html`。PyInstaller、uv 及构建工具由独立 release 依赖组和 `uv.lock` 固定，普通服务不引入这些构建依赖。

只嵌入包内运行源码/资源、pypdf 原始库、网页、uv 工具和构建清单，不收集整个仓库、`.env`、数据库或工作区。迁移、容器程序、依赖闭包校验及标准库清单必须有原始文件，不能只保留模块字节码。`build.json` 保存资源和 uv 的 SHA；旁置 `.manifest.json` 另记录最终可执行文件摘要。Windows 使用 PyInstaller 的 Python 入口传参，避免资源清单超过命令行长度限制。

## 启动及配置

直接运行可执行文件，默认进入 app 模式；源代码方式为 `uv run --locked openresearch app`。`serve`、`worker`、`sandbox` 等独立命令继续保留，可执行文件也支持这些子命令。工作台默认监听 `http://127.0.0.1:8080/`。

发行物默认读取可执行文件旁的 `.env`，默认把持久数据写入同目录 `.data`，不写入临时解压目录。可以用环境变量覆盖，或明确指定外部配置文件：

```powershell
./openresearch.exe --env-file C:/OpenResearch/local.env app
```

`OPENRESEARCH_PORT`、`OPENRESEARCH_DATA_DIR`、`OPENRESEARCH_API_TOKEN`、模型与运行时字段沿用现有配置。显式相对路径按启动时工作目录解析；默认数据目录始终跟随发行物位置。不要把配置文件或运行数据添加到构建清单。

默认本地沙箱只监听随机 loopback 端口，保留同一个已绑定 socket 防止端口竞争，并使用临时随机服务令牌。API 和 Worker 使用原 HTTP 适配器；配置 HTTP/OpenSandbox 后端或明确 `OPENRESEARCH_SANDBOX_URL` 时，尊重外部服务，不另外启动本地沙箱。默认 Docker 模式不自动扩权启用 OpenSandbox 回退，auto 模式仍受原开关限制。

审批、身份归属、持久队列、预算、运行时和实际产物校验沿用原契约。一体化启动不等于自动批准所有任务。普通提供源码的工作流不会在没有模型、也没有依赖声明时猜测安装包；需要声明依赖或配置模型恢复。

Ctrl+C、SIGTERM 和 Windows Ctrl+Break 触发退出。先停止 Worker 的活动任务和容器，再关闭共享数据库，最后停止本地沙箱。自动化父进程可用 `--exit-on-stdin-close`，保持输入管道打开，关闭管道后触发同一退出流程；这不是远程关闭 API。组件失败会终止一体化应用，启动失败返回错误，不保留只有网页而 Worker 已退出的进程。

## 实际发行物验收

```powershell
uv run --locked --group release python scripts/audit_release.py dist/release/openresearch.exe
uv run --locked --group release python scripts/verify_release.py dist/release/openresearch.exe
```

Linux 使用没有 `.exe` 后缀的路径。归档检查解开实际数据及模块，核对所有运行资源和 uv 摘要，拒绝私人文件或当前配置密钥。运行验收把单个程序复制到独立目录，以另一个空工作目录和空 PATH 启动，不提供项目网页、Python、Node、uv 或配置文件。

运行验收通过真实 HTTP 检查内嵌网页及静态字节、未知 API 的 404、审批、原生代码执行、显式依赖解析/锁定/容器安装、原程序断言、容器内 PDF 解析、退出清理及重启不重复。空白 PDF 是功能夹具，继续标记需要 OCR；这些程序也不是论文效果证明。回执和原失败日志保存在忽略目录 `.data/release-acceptance/`，不随发行物提交。

Linux 验收第一次关闭标准输入退出，重启并核对持久记录后，第二次保持标准输入打开并发送 `SIGTERM`。两次均要求退出码 0、API 端口释放及所属容器为零；回执记录实际使用的 `shutdown_modes`。Windows 继续使用标准输入关闭流程。

验收中的公开依赖下载可以显式传入 `OPENRESEARCH_SOURCE_PROXY_URL` 和 `OPENRESEARCH_SOURCE_DOWNLOAD_TIMEOUT_SECONDS` 环境变量；它们只配置来源网络。验收子进程仍不读取项目 `.env`，模型、数据库和其它应用配置不继承。宿主验收使用宿主可达的代理地址，Docker 部署中的 `host.docker.internal` 需按本机情况换成 loopback 地址。

CI 分别构建 Windows/Linux 发行物并检查实际归档；Linux 作业运行真实 Docker 验收，Windows 作业检查冻结 CLI。本机 Windows 的完整容器验收和远程 Linux 结果分别记录，不把 CLI 启动当作完整实验验证。构建工件通过 Actions 保存七天，不自动发布 Release。

PyInstaller 的资源路径和按平台构建行为见 [运行资源说明](https://pyinstaller.org/en/stable/runtime-information.html) 与 [发行工作方式](https://pyinstaller.org/en/stable/operating-mode.html)。
