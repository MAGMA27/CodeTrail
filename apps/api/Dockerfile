FROM python:3.12-slim
WORKDIR /workspace
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv
COPY . .
RUN uv sync --project apps/api --locked
RUN mkdir -p apps/web && npm install --prefix apps/web --no-save --no-package-lock typescript@5.9.3
ENV CODETRAIL_DATA_DIR=/data
ENV CODETRAIL_TYPESCRIPT_COMPILER=/workspace/apps/web/node_modules/typescript/lib/typescript.js
EXPOSE 8000
