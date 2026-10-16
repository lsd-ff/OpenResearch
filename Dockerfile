FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
# 固定工具版本；直接使用官方 PyPI wheel，避免额外镜像仓库成为构建前提。
RUN python -m pip install --no-cache-dir --only-binary=:all: uv==0.11.16
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    OPENRESEARCH_HOST=0.0.0.0 \
    OPENRESEARCH_DATA_DIR=/data \
    OPENRESEARCH_WEB_DIR=/app/frontend/dist
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ src/
RUN uv sync --locked --no-dev
COPY --from=frontend /frontend/dist /app/frontend/dist
EXPOSE 8080
CMD ["/app/.venv/bin/openresearch", "serve"]
