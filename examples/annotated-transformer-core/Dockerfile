FROM python:3.12-slim
RUN python -m pip install --no-cache-dir --only-binary=:all: --retries 2 --timeout 60 torch==2.7.1+cpu --index-url https://download.pytorch.org/whl/cpu
