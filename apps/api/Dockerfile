FROM node:24-bookworm-slim AS typescript
WORKDIR /tmp/typescript
RUN npm install --no-save --no-package-lock typescript@5.9.3

FROM python:3.12-slim
WORKDIR /workspace
RUN pip install --no-cache-dir uv
COPY apps/api/pyproject.toml apps/api/uv.lock ./apps/api/
RUN uv sync --project apps/api --locked --no-install-project
COPY . .
RUN uv sync --project apps/api --locked
COPY --from=typescript /usr/local/bin/node /usr/local/bin/node
COPY --from=typescript /tmp/typescript/node_modules/typescript /workspace/apps/web/node_modules/typescript
ENV CODETRAIL_DATA_DIR=/data
ENV CODETRAIL_TYPESCRIPT_COMPILER=/workspace/apps/web/node_modules/typescript/lib/typescript.js
EXPOSE 8000
