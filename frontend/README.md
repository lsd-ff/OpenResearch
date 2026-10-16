# Python 科研工作台

基于原 ScholarAgent React 工作台独立复制，连接 Python API，本目录不依赖 Go 服务。

使用 Node.js 22，运行 npm ci、npm run lint、npm run build。开发使用 npm run dev，默认 API 为 http://127.0.0.1:8080，可设置 VITE_API_BASE_URL。Python .env 配置 OPENRESEARCH_WEB_DIR=frontend/dist 后提供同源页面；根目录 Dockerfile 已包含前端构建。

对话区上传 PDF，通过「配置实验」阅读附件，配置仓库、完整固定依赖和评估数据。生成后运行整图，节点面板展示日志、报告与证据。刷新会从 API 重新读取当前计划。

划词和全文翻译调用真实模型接口；全文限定 100 页、12000 字符，返回内容放入会话。未配置模型、超限或请求失败会明确报错，不显示演示译文。

「连接设置」在内存中接收共享 API 令牌，以认证 Cookie 支持 REST/SSE/PDF；不把令牌写入浏览器存储。会话和游客 ID 使用独立 openresearch-python 前缀。设置用户 ID 建立新会话，用户 ID 本身不是账号登录。

PDF worker 从构建产物加载，关闭 PDF.js 动态表达式执行。当前保留原阅读器依赖，构建仍提示打包中的 eval 与大 chunk；未对整个依赖树完成安全审计。独立 nginx 配置面向同网络 api:8080；默认 Compose 由 API 直接提供页面，无需额外 nginx。
