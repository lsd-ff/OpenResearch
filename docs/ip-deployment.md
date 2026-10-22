# Linux 服务器一键部署与 IP 访问

本工具使用 Docker Compose 启动 API、Worker、原生 Docker 沙箱和 Nginx。只有 Nginx 发布公网端口，API 和沙箱不发布宿主端口。宝塔已有网站及 Nginx 配置不需要修改。

适用于个人或少量可信使用者。默认实验并发为 1，执行前需要审批，不启用 OpenSandbox。当前 HTTP 入口适合有限范围测试；正式公开服务应接入 HTTPS，并满足服务商的备案要求。

## 前置条件

- Linux root 终端，Python 3.9 或更新版本、Git、curl。
- 已启动的 Docker Engine，以及支持 `!reset` 和 `up --wait` 的 Docker Compose。
- 足够的磁盘空间和内存；4 核 / 8 GB 可以先用于小规模 CPU 实验。
- 服务器能访问 GitHub、Docker Hub、npm、PyPI 和所配置的模型接口。
- 在腾讯云安全组放行 TCP 18080，测试时建议来源限制为使用者公网 IP `/32`。脚本不修改云安全组或主机防火墙，也不以本机探活证明公网可达。

不需要在宿主机安装项目运行环境或额外安装宝塔 Nginx。项目后端运行在镜像里的 Python 3.12 环境，宿主 Python 只运行部署工具。

## 执行

先将本仓库的 [`scripts/deploy_ip.py`](../scripts/deploy_ip.py) 下载到服务器 `/root/openresearch-deploy.py`，然后运行：

```bash
python3 /root/openresearch-deploy.py deploy \
  --public-ip 124.220.186.138 \
  --port 18080 \
  --llm-base-url https://maas.qianwenaiapi.com/compatible-mode/v1 \
  --model qwen3.8-flash
```

首次执行会提示输入模型 API Key，输入内容不回显。已有有效密钥和访问令牌会复用；脚本生成缺失的网页访问令牌及内部沙箱令牌，不会把模型密钥打印到终端。

首次克隆默认使用 main；可用 `--ref` 选择指定版本。已有仓库使用当前提交，不自动拉取、切换分支或重置代码。存在未提交的代码修改、非目标仓库、未知运行目录或被其他服务占用的端口时停止，不删除原服务。

部署依次进行配置校验、镜像下载、真实应用构建、后端启动、前端提取、Nginx 配置校验、入口健康及认证验收、真实沙箱代码执行和清理、真实模型调用。失败返回非零状态并保留现场。`--skip-model-check` 仅跳过模型调用，输出会明确标记尚未验证；不要把跳过当成模型可用的证据。

## 浏览器连接

打开 `http://124.220.186.138:18080`，在连接设置中填写下面文件里的网页访问令牌：

```bash
cat /opt/openresearch-deployment/access-token.txt
```

该令牌与模型 API Key 不同。连接成功后刷新页面，再尝试普通聊天、审批计划和实际代码执行。脚本健康检查不代替科研任务的完整验收。

## 配置、数据与维护

脚本保留根目录 `.env`；首次将其复制到 `/opt/openresearch-deployment/runtime.env`，后续以这个运行文件为准。工具管理的配置集中在文件尾部标记块内，重新部署会更新该块，用户其他配置会保留。若需修改工具管理的模型地址或名称，使用对应命令参数；修改模型密钥时编辑运行文件中最后生效的同名配置。

`/opt/openresearch-deployment` 是 0700 私有目录，运行配置和令牌是 0600 文件。这个目录位于源码目录之外，不进入 Git 或 Docker 构建上下文。不要将它上传到公共空间。应用配置仍由项目的基础 Compose 和已有 override 合并，部署专用配置最后应用。自定义部署目录时，运行目录为源码目录旁边的同名目录加 `-deployment` 后缀。

原 Compose 项目名和数据卷名称会保留。每次重新部署先备份运行配置到 `/opt/openresearch-deployment/backups/`，静态前端使用新的版本目录；这些不是业务数据备份。SQLite、上传、产物及沙箱数据仍需在没有任务运行时做数据卷备份，并保存到服务器外。

```bash
python3 /root/openresearch-deploy.py status
python3 /root/openresearch-deploy.py logs
python3 /root/openresearch-deploy.py restart
```

`restart` 使用 `compose up -d --wait` 重新应用运行配置。不要直接使用默认 `docker compose` 管理这套部署，因为它不会自动加载私有运行文件和部署覆盖配置。

升级前先备份业务数据，等待任务结束，手动更新仓库，再重新执行部署命令。重跑可能重建服务，因此不要在任务执行期间操作。脚本不执行 `down -v`、删除数据卷或自动迁移回退；更新失败时按保存的版本与数据备份恢复。

如果先前手动部署的宝塔 Nginx 已占用 18080，脚本会停止。可先保留旧站点，改用 `--port 18090`，并放行对应安全组端口；它不会自动关闭已有站点。

## 下载 uv 超时后的续跑

若构建停在 `pip install uv==0.11.16`，且日志出现 `files.pythonhosted.org` 读取超时，可在运行文件 `/opt/openresearch-deployment/runtime.env` 的工具标记块外新增：

```dotenv
OPENRESEARCH_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

先在源码目录执行 `git pull --ff-only`，获取支持该构建参数的 Dockerfile 与 Compose 配置，再重新运行部署命令，可追加 `--non-interactive` 复用已保存的模型密钥。已有完成的构建层会复用；不要修改 Dockerfile 后直接重跑，因为工具会保护存在未提交代码改动的仓库。

该选项仅切换安装 uv 的 pip 索引，不会改写 `uv.lock` 中运行依赖的下载地址、版本或哈希。pip 下载读取超时设为 180 秒、连接重试 5 次；后续 uv 同步同样延长读取超时和重试。保持 HTTPS 证书校验。镜像可用性及服务器网络仍需以实际构建结果为准。

## 本地验收范围

2026-10-22 在 WSL2 Linux 和真实 Docker 中完成应用构建、四服务启动、首页及 API 健康检查、未认证请求 401、重复部署、数据卷文件保留、访问令牌保留及 0700/0600 文件权限检查。沙箱实际执行了求和断言并成功清理。部署工具另有 15 项行为测试，覆盖配置保留、密钥中的特殊字符、端口占用、仓库改名兼容及失败判断。

本地验收没有使用用户的模型密钥，明确跳过了模型调用；腾讯云目标服务器及所提供模型服务尚需用户运行脚本后实际验证。
