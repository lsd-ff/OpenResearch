FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
# 国内服务器可选公开 pip 镜像；仍固定 uv 版本，并保持 HTTPS 校验。
ARG OPENRESEARCH_PIP_INDEX_URL=https://pypi.org/simple
RUN python -m pip install --no-cache-dir --only-binary=:all: \
    --index-url "${OPENRESEARCH_PIP_INDEX_URL}" --timeout 180 --retries 5 uv==0.11.16
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    OPENRESEARCH_HOST=0.0.0.0 \
    OPENRESEARCH_DATA_DIR=/data \
    OPENRESEARCH_WEB_DIR=/app/frontend/dist
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ src/
# 运行依赖继续按 uv.lock 的来源与哈希安装，容忍慢速链路而不重写锁文件。
RUN UV_HTTP_TIMEOUT=180 UV_HTTP_RETRIES=5 uv sync --locked --no-dev
COPY --from=frontend /frontend/dist /app/frontend/dist
EXPOSE 8080
CMD ["/app/.venv/bin/openresearch", "serve"]
